"""Package and portable core primitive checks."""

from datetime import UTC

import satori
from satori.core.clock import Clock, SystemClock
from satori.core.ids import IdGenerator, Uuid4Generator


def test_package_imports() -> None:
    """The installed src-layout package imports without side effects."""

    assert satori.__all__ == ()


def test_core_primitives_satisfy_their_ports() -> None:
    """Portable system adapters honor framework-independent protocols."""

    clock = SystemClock()
    generator = Uuid4Generator()

    assert isinstance(clock, Clock)
    assert isinstance(generator, IdGenerator)
    assert clock.now().tzinfo is UTC
    assert len(generator.new()) == 36
