"""Scope and clock-source backends for tools/bench/bench.py.

Two interfaces, each with a dry backend that records what it was asked to do and
measures nothing. The real backends are stubs: the Rigol DS1202Z-E is driven
over SCPI (USBTMC or LAN; pyvisa or a raw socket on port 5555) and the ES-8
clock through Ableton Live. In the maintainer's environment both are reachable
through control bridges outside this repository; wiring them in is the first
task of the baseline bench session, with the module present.
"""


class BackendUnavailable(RuntimeError):
    pass


class Scope:
    def identify(self):
        raise NotImplementedError

    def configure(self, scope_settings, probes):
        raise NotImplementedError

    def measure(self, items):
        """Return {measurement name: value} for the listed items."""
        raise NotImplementedError


class ClockSource:
    def identify(self):
        raise NotImplementedError

    def set_clock(self, bpm, ppqn):
        raise NotImplementedError

    def arm_reset(self, description):
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class DryScope(Scope):
    def __init__(self):
        self.log = []

    def identify(self):
        return "dry scope (no hardware)"

    def configure(self, scope_settings, probes):
        self.log.append(("configure", dict(scope_settings), dict(probes)))

    def measure(self, items):
        self.log.append(("measure", list(items)))
        return {item: None for item in items}


class DryClock(ClockSource):
    def __init__(self):
        self.log = []

    def identify(self):
        return "dry clock source (no hardware)"

    def set_clock(self, bpm, ppqn):
        self.log.append(("set_clock", bpm, ppqn))

    def arm_reset(self, description):
        self.log.append(("arm_reset", description))

    def start(self):
        self.log.append(("start",))

    def stop(self):
        self.log.append(("stop",))


class RigolScpiScope(Scope):
    """Rigol DS1202Z-E over SCPI. Not implemented: needs the bench session."""

    def __init__(self, resource):
        raise BackendUnavailable(
            "the Rigol SCPI backend is not implemented yet; resource %r. "
            "Run with --dry-run, or implement RigolScpiScope in tools/bench/backends.py" % resource)


class AbletonEs8Clock(ClockSource):
    """CLOCK and RESET from Ableton Live through an Expert Sleepers ES-8. Not implemented."""

    def __init__(self):
        raise BackendUnavailable(
            "the Ableton/ES-8 clock backend is not implemented yet. "
            "Run with --dry-run, or implement AbletonEs8Clock in tools/bench/backends.py")


def make_scope(name):
    if name in ("dry", "none"):
        if name == "none":
            raise BackendUnavailable("no scope backend selected (--scope-backend dry|rigol:<visa resource>)")
        return DryScope()
    if name.startswith("rigol:"):
        return RigolScpiScope(name.split(":", 1)[1])
    raise BackendUnavailable("unknown scope backend %r" % name)


def make_clock(name):
    if name in ("dry", "none"):
        if name == "none":
            raise BackendUnavailable("no clock backend selected (--clock-backend dry|ableton)")
        return DryClock()
    if name == "ableton":
        return AbletonEs8Clock()
    raise BackendUnavailable("unknown clock backend %r" % name)
