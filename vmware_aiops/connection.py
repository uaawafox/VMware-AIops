"""Connection management for vCenter and ESXi hosts.

Handles multi-target connections via pyVmomi, with per-target session pools.
"""

from __future__ import annotations

import atexit
import socket
import ssl
from collections.abc import Callable
from typing import TYPE_CHECKING

from pyVmomi import vim
from vmware_policy import sanitize
from vmware_policy.access import login_has_no_session, no_role_message
from vmware_policy.pool import SessionPool

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_aiops.config import CONFIG_FILE, AppConfig, ConfigError, TargetConfig, load_config

# Component 6: per-request backend-credential routing (shared resolver lib).
# Optional - absent in local/stdio dev; routing then no-ops and the startup
# config credentials are used. The hub's per-role vCenter accounts live in 1P
# as "MCP - <role> - <backend>"; syseng-only per the C5 validator ACCESS matrix;
# the backend account's own RBAC enforces read-vs-write (aiops is
# write-capable, so only syseng_elevated reaches it). Upstream 2.4's roles are
# per-token tool/target allow-lists over ONE service account; they do not
# replace this credential swap, so the patch carries across the 2.x sync.
try:
    import uaa_hub_routing

    _HUB_ROUTING = True
    _VCENTER_SELECTOR = uaa_hub_routing.priority_selector(
        ("syseng_elevated", "syseng_readonly")
    )
except ImportError:
    _HUB_ROUTING = False


class NoVSphereRoleError(ConfigError):
    """The login was accepted but the account can read nothing (AIops#47).

    That is what an account with no vSphere role looks like: no session, a
    container view on the root folder refused, and every inventory call that
    follows fails with ``NotAuthenticated``, whose usual remedy
    (check the password) sends the operator the wrong way. Raised at connect
    time so doctor fails on it and the pool never caches such a session. A
    ``ConfigError`` so the CLI and ``_safe_error`` pass its message whole; a
    class of its own so the cross-vCenter roll-up, which reports class names
    only, says this rather than a generic config problem.
    """


def login_refused_message(
    target: str, cli: str, *, privilege: str | None = None, esxi: bool = False
) -> str:
    """The teaching error when the login itself is refused with NoPermission.

    vCenter 8.0.3 refuses the login, with NoPermission for System.View on the
    root folder, for an SSO account holding no vSphere permission anywhere (lab,
    2026-10-18); an account with a permission lower down still logs in. Only
    that case gets the "no vSphere role" remedy. An ESXi host (lockdown mode,
    host permissions) or another privilege gets a neutral text naming the
    privilege vCenter returned (sanitized, capped at 48). About 300 characters.
    """
    if not esxi and privilege == "System.View":
        return (
            f"'{target}' refused the login: the password is right but the account has "
            "no vSphere role (no System.View on the root). Assign Read-Only or higher "
            f"(vCenter: Global Permissions), then run '{cli} doctor'. A new password "
            "will not help."
        )
    # vCenter's own string: sanitized and bounded so the text stays short.
    need = f" ({sanitize(privilege, 48)})" if privilege else ""
    return (
        f"'{target}' refused the login with NoPermission{need}: the password is right "
        "but this account may not log in there. On ESXi check the host's Permissions "
        f"and lockdown mode; on vCenter assign a role. Then run '{cli} doctor'."
    )


#: What a NotAuthenticated fault raised after connecting means. Two causes look
#: identical here: the session expired, or the account's vSphere role was
#: removed (AIops#47). A retry tells them apart, because the next login fails
#: with the role message when there is no role. "Check credentials" fits neither.
NOT_AUTHENTICATED_HINT = (
    "The vSphere session is not authenticated. Either it expired: retry, and a new "
    "session is opened. Or the account has no vSphere role: assign one (Read-Only or "
    "higher) on the vCenter root folder; the retry says so if that is the cause. "
    "Changing the password will not help."
)


# ServiceInstance is a pyVmomi ManagedObject — its __setattr__ rejects any
# attribute not in its allowed list (raises "Managed object attributes are
# read-only" on pyVmomi 8.x). We keep per-connection metadata in this module
# dict, keyed by id(si). Cleared via atexit when the SI is disconnected.
# 踩坑 #32 (2026-05-19, 客户 vCenter 8.0U3 现场).
_SI_VERIFY_SSL: dict[int, bool] = {}

# atexit cleanups for live connections, keyed by id(si) so a connection dropped
# before interpreter exit can take its handler with it.
_SI_ATEXIT: dict[int, Callable[[], None]] = {}


def _release_si(si: ServiceInstance) -> None:
    """Unregister the atexit cleanup registered for ``si``.

    Every connect() registers a cleanup that closes over si, and atexit holds
    that closure -- and therefore si -- until the process exits. A long-running
    MCP server that reconnects after each session expiry (踩坑 #40) accumulates
    one dead ServiceInstance and one handler per reconnect, and at exit runs a
    Disconnect against every session it ever opened.

    Measured before this existed: 50 evict-and-reconnect cycles left 50 handlers
    registered and all 50 evicted ServiceInstance objects still reachable, while
    the id(si) side stores stayed correctly at one entry -- the side-store
    discipline was never the leak, the registration was.

    Boundary: this covers the paths that drop a *connection* -- the session
    pool's close (dead-session eviction, disconnect, a call's extra session). A
    caller that drops the whole ConnectionManager while it still holds
    connections leaks them exactly as before, since both atexit and this table
    still reach them. Unchanged rather
    than fixed: the reconnect loop is the shape that grows without bound.
    """
    fn = _SI_ATEXIT.pop(id(si), None)
    if fn is not None:
        atexit.unregister(fn)



def get_verify_ssl(si: ServiceInstance) -> bool:
    """Return verify_ssl flag stashed by the connect() that created ``si``.

    Defaults to True (strict) if the SI was created outside this manager.
    """
    return _SI_VERIFY_SSL.get(id(si), True)


def _session_alive(si: ServiceInstance) -> bool:
    """Liveness probe for a pooled session.

    An expired token can surface as a ``None`` currentSession instead of raising
    (踩坑 #40), and any failure (NotAuthenticated, socket error, …) means the
    session is unusable.
    """
    try:
        return si.content.sessionManager.currentSession is not None
    except Exception:
        return False


def _close_session(si: ServiceInstance) -> None:
    """Drop a session: clear its id(si) side stores, then log it out.

    Called by the pool for an evicted dead session, on disconnect, and for a
    call's temporary extra session. The verify_ssl entry goes NOW rather than at
    atexit: once the old si is GC'd, a new si for a DIFFERENT target can reuse
    the same id() value and read stale verify_ssl (id-reuse hazard). Never
    raises: the session may already be dead.
    """
    from pyVim.connect import Disconnect

    _SI_VERIFY_SSL.pop(id(si), None)
    _release_si(si)
    try:
        Disconnect(si)
    except Exception:
        pass


# config.yaml target name -> hub-routing backend name. Per-role cred items are
# per-vCenter ("MCP - <role> - <backend>"), so a routed request must resolve the
# items for the vCenter it is about to log in to. Approved by Allen in-session
# 2026-07-15. The target was renamed v9-vcenter -> uaa-vcenter on 2026-08-03 and
# the 1P items kept their "vcenter-v9" title.
#
# A routed request for a target with no row here is DENIED (fail closed). It
# used to fall back to the "vcenter-prod" items, which belonged to the apps
# vCenter decommissioned 2026-08-24: a stale default that would put another
# vCenter's credentials on this host. Unrouted calls (no hub signal) are
# unaffected and keep the startup credential.
_ROUTING_BACKENDS = {"uaa-vcenter": "vcenter-v9"}

# Pool keys: "<target>" for an unrouted session, "<target>#<1P item title>" for
# a routed one, so two hub roles never share a vCenter session and a routed
# session is never handed to an unrouted caller. The pool owns liveness and
# eviction for both shapes (the fork's own probe/evict block went with 2.3.0).
# The key is never PARSED: connect() records what each key means before asking
# the pool, so a target whose own name contains the separator still works.
_ROUTED_KEY_SEP = "#"


class ConnectionManager:
    """Manages connections to multiple vCenter/ESXi targets.

    Sessions come from a :class:`vmware_policy.pool.SessionPool` keyed by target
    name (HLD §16 D-13(2)): inside an MCP call scope each call holds its own
    session for the whole call, so two calls never interleave SOAP on one
    connection; outside one (CLI, stdio, the daemon) one cached session per
    target is reused.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        # pool key -> (target name, routed 1P item or None); written by connect()
        # before the pool is asked, read by the create callback and disconnect().
        self._key_meaning: dict[str, tuple[str, str | None]] = {}
        self._pool: SessionPool[ServiceInstance] = SessionPool(
            "vmware-aiops",
            # Looked up on self at call time so a test can patch _create_connection.
            create=lambda key: self._create_for_key(key),
            is_alive=_session_alive,
            close=_close_session,
        )

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> ServiceInstance:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        backend = _ROUTING_BACKENDS.get(target.name)
        routed = (
            uaa_hub_routing.routing_item(backend or "unmapped", _VCENTER_SELECTOR)
            if _HUB_ROUTING
            else None
        )
        if routed is not None and backend is None:
            raise uaa_hub_routing.RoutingError(
                f"target {target.name!r} has no hub-routing backend; routed request denied"
            )
        # The item must be for THIS target's backend. X-Hub-Roles builds it
        # from our backend name, but the legacy single-role header carries a
        # pre-resolved title verbatim; one minted for another vCenter must not
        # put that vCenter's credentials on this host.
        if routed is not None and not routed.endswith(f" - {backend}"):
            raise uaa_hub_routing.RoutingError(
                f"routing item {routed!r} is not for backend {backend!r}; routed request denied"
            )
        key = target.name if routed is None else f"{target.name}{_ROUTED_KEY_SEP}{routed}"
        self._key_meaning[key] = (target.name, routed)
        return self._pool.get(key)

    def _create_for_key(self, key: str) -> ServiceInstance:
        """Pool factory: open the session a pool key describes.

        ``<target>`` logs in with the startup config credential;
        ``<target>#<item>`` resolves the per-role 1P item first and logs in as
        that account. A routed item missing either half is refused before any
        login (fail closed), exactly as the pre-2.x fork did on a cache miss.
        """
        name, item = self._key_meaning.get(key, (key, None))
        target = self._config.get_target(name)
        if not item:
            return self._create_connection(target)
        fields = uaa_hub_routing.resolve_fields(item)
        user, pw = fields.get("username"), fields.get("password")
        if not user or not pw:
            raise uaa_hub_routing.RoutingError(f"1P item {item!r} missing username/password")
        return self._create_connection(target, user=user, pwd=pw)

    def disconnect(self, target_name: str) -> None:
        """Disconnect from a specific target (busy sessions close when their call ends)."""
        self._pool.discard(target_name)
        for key in self._pool.keys():
            if key != target_name and self._key_meaning.get(key, (None,))[0] == target_name:
                self._pool.discard(key)

    def disconnect_all(self) -> None:
        """Disconnect from all targets."""
        self._pool.close_all()

    def list_targets(self) -> list[str]:
        """List all configured target names."""
        return [t.name for t in self._config.targets]

    def connect_all(self) -> tuple[list[tuple[str, ServiceInstance]], list[tuple[str, str]]]:
        """Connect to every configured target, tolerating per-target failures.

        Returns ``(sessions, unreachable)`` — ``[(name, si)]`` for targets that
        connected and ``[(name, reason)]`` for those that did not — so the
        cross-vCenter attention view degrades gracefully (one dead vCenter never
        sinks the roll-up). The reason is class-name only, so no host:port or
        credential detail leaks.
        """
        sessions: list[tuple[str, ServiceInstance]] = []
        unreachable: list[tuple[str, str]] = []
        for name in self.list_targets():
            try:
                sessions.append((name, self.connect(name)))
            except Exception as e:  # noqa: BLE001 — any connect failure degrades to "unreachable"
                unreachable.append((name, type(e).__name__))
        return sessions, unreachable

    def list_connected(self) -> list[str]:
        """List currently connected target names."""
        return self._pool.keys()

    @staticmethod
    def _create_connection(
        target: TargetConfig, *, user: str | None = None, pwd: str | None = None
    ) -> ServiceInstance:
        """Create a new pyVmomi connection.

        ``user``/``pwd`` override the target's startup credentials (Component 6
        per-role routing); when omitted, the target's own username + env
        password are used (legacy/startup path).
        """
        from pyVim.connect import Disconnect, SmartConnect

        context = None
        if not target.verify_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        # Resolve credentials BEFORE the try block. Both are properties, and
        # the missing-password one raises ConfigError — an OSError subclass the
        # handlers below would otherwise relabel as a TLS/DNS failure, burying
        # this family's most common first-run error behind the wrong remedy.
        # Read adjacently so a sidecar rotating both halves cannot split them.
        # A routed per-request pair (Component 6, passed in by the pool factory)
        # takes precedence, but only as a COMPLETE pair — mixing a routed half
        # with a config half logs in as nobody.
        if user is None or pwd is None:
            user, pwd = target.username, target.password

        try:
            si = SmartConnect(
                host=target.host,
                user=user,
                pwd=pwd,
                port=target.port,
                sslContext=context,
                disableSslCertValidation=not target.verify_ssl,
            )
        # These three carry the certificate subject, the unresolved hostname
        # and the full host:port respectively. _safe_error no longer passes
        # bare OSError through, so an agent would see only the class name —
        # translate to authored text that names the target and the setting to
        # change, and never interpolates the original exception. The raw detail
        # stays on __cause__, which only reaches the server-side log.
        except ssl.SSLError as exc:
            raise ConfigError(
                f"TLS verification failed for target '{target.name}' — set "
                f"verify_ssl: false on that target in {CONFIG_FILE} if it uses a "
                f"self-signed certificate, or install its CA on this host."
            ) from exc
        except socket.gaierror as exc:
            raise ConfigError(
                f"Could not resolve the host configured for target '{target.name}' "
                f"— check that target's 'host' value in {CONFIG_FILE} for a typo "
                f"or a DNS suffix this machine cannot resolve."
            ) from exc
        except OSError as exc:
            raise ConnectionError(
                f"Could not reach target '{target.name}' — check that the "
                f"vCenter/ESXi host is up and that its 'host' and 'port' in "
                f"{CONFIG_FILE} are reachable from this machine."
            ) from exc
        except vim.fault.NoPermission as exc:
            # A wrong password is InvalidLogin, not this; it passes through.
            raise NoVSphereRoleError(
                login_refused_message(
                    target.name,
                    "vmware-aiops",
                    privilege=getattr(exc, "privilegeId", None),
                    esxi=target.type == "esxi",
                )
            ) from exc

        # Stash verify_ssl in module dict (NOT on si — pyVmomi 8.x rejects
        # setattr on ManagedObject, see 踩坑 #32). Consumers in ops/* read via
        # get_verify_ssl(si).
        _SI_VERIFY_SSL[id(si)] = target.verify_ssl

        def _cleanup(_si: ServiceInstance = si) -> None:
            _SI_VERIFY_SSL.pop(id(_si), None)
            try:
                Disconnect(_si)
            except Exception:
                pass

        _SI_ATEXIT[id(si)] = _cleanup
        atexit.register(_cleanup)
        if login_has_no_session(si):
            # Closed exactly as the pool closes a session (side stores, atexit
            # handler, logout), so nothing outlives the refusal.
            _close_session(si)
            raise NoVSphereRoleError(no_role_message(target.name, "vmware-aiops"))
        return si


def get_content(si: ServiceInstance) -> vim.ServiceInstanceContent:
    """Shortcut to get ServiceContent from a ServiceInstance."""
    return si.RetrieveContent()
