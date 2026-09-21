import asyncio
import threading
import numpy as np
import pytest
from hyperloom_bridge.session import Session
from hyperloom_core import Graph
from hyperloom_core.algorithms.layout import LayoutStep


@pytest.mark.asyncio
async def test_layout_runs_off_event_loop_and_respects_backpressure(monkeypatch):
    event_thread = threading.get_ident()
    generated = []
    def steps(*args, **kwargs):
        for i in range(3):
            assert threading.get_ident() != event_thread
            generated.append(i)
            yield LayoutStep(i + 1, np.zeros((1, 2)), False)
    monkeypatch.setattr("hyperloom_bridge.session.force_directed_layout", steps)
    sent = 0
    async def send(data):
        nonlocal sent
        # First message is the graph. No next step may be computed while
        # send is awaiting a slow consumer.
        before = len(generated)
        await asyncio.sleep(.01)
        assert len(generated) == before
        sent += 1
    await Session(Graph()).stream_to(send)
    assert sent == 4
