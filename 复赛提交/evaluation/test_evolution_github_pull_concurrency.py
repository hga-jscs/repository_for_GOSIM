"""REQ-6 terminal state under two real competing authorized requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from evolution_github_helpers import data
from test_evolution_github_pull_boundaries import proposal


@pytest.mark.requirements("REQ-6-5", "REQ-6-6")
def test_evolution_github_merge_and_close_keep_terminal_state(proposal):
    clients, root = proposal["clients"], proposal["root"]
    endpoint = proposal["pull"]
    for number in range(12):
        if number:
            data(clients["Write"].post(root + "/files", json={"branch": "feature", "path": f"next-{number}.txt", "content": "Next source change", "message": "Next source commit"}))
            created = data(clients["Write"].post(root + "/pulls", json={"base": "main", "compare": "feature", "title": f"Competing transitions {number}"}))
            endpoint = root + "/pulls/" + str(created["number"])
        barrier = Barrier(2)
        actions = (("Admin", "merge"), ("Maintain", "close")) if number % 2 else (("Maintain", "merge"), ("Admin", "close"))
        def transition(action):
            role, operation = action
            barrier.wait(timeout=10)
            response = clients[role].post(endpoint + "/" + operation)
            return operation, response.status_code, response.text
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(transition, actions))
        state = data(clients["Admin"].get(endpoint))["pr"]["status"]
        assert sorted(result[1] for result in results) == [200, 409], (results, state)
        winner = next(result[0] for result in results if result[1] == 200)
        assert state == ("Merged" if winner == "merge" else "Closed"), results
