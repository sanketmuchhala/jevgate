import json
import unittest
import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
from src.questions import BATTERY
from src.schemas import NoulSignal, ScoreSignal
from src.system_one import JevSystemOne, SystemOneError
from tests.fakes import jev_payload, signals


class JevAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Drives the real TypeSafe SDK against a mock HTTP transport, so the wire format is tested too."""

    async def evaluate(self, handler, state=None):
        async with AsyncTypeSafeClient(
            api_key="test-key", transport=httpx2.MockTransport(handler), compression=False,
            retry=RetryPolicy(max_retries=0),
        ) as client:
            adapter = JevSystemOne(client, BATTERY, model="jev-1.14.0")
            return await adapter.evaluate(state or {"document": "memo", "metadata": {"source_unit": "ops"}})

    async def test_one_request_carries_every_question_with_the_pinned_model(self):
        sent = []

        def handler(request):
            sent.append(request)
            return httpx2.Response(200, json=jev_payload(signals(personal_data=0.81)),
                                   headers={"x-typesafe-request-id": "req_123"})

        result = await self.evaluate(handler)
        (request,) = sent
        body = json.loads(request.content)
        self.assertEqual(request.url.path, "/v1/systemone")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "jev-1.14.0")
        self.assertEqual(body["state"], {"document": "memo", "metadata": {"source_unit": "ops"}})
        self.assertEqual(set(body["questions"]), set(BATTERY))
        self.assertEqual(
            {q["type"] for q in body["questions"].values()}, {"choice", "score", "noul"},
        )
        self.assertEqual(body["questions"]["sensitivity"]["criteria"], BATTERY["sensitivity"].levels)

        self.assertEqual((result.model, result.request_id, result.input_tokens), ("jev-1.14.0", "req_123", 1234))
        noul = result.signals["personal_data"]
        self.assertIsInstance(noul, NoulSignal)
        self.assertAlmostEqual(noul.confidence, 0.62)  # |2 * 0.81 - 1|, derived by JevGate
        sens = result.signals["sensitivity"]
        self.assertIsInstance(sens, ScoreSignal)
        self.assertEqual(sorted(sens.probabilities), [0, 1, 2, 3])

    async def test_missing_answer_is_an_error_not_a_default(self):
        payload = jev_payload(signals())
        del payload["answers"]["movement_details"]
        with self.assertRaisesRegex(SystemOneError, "movement_details: expected a noul answer"):
            await self.evaluate(lambda request: httpx2.Response(200, json=payload))

    async def test_wrong_answer_type_is_an_error(self):
        payload = jev_payload(signals())
        payload["answers"]["urgency"] = {"type": "noul", "noul": 0.4}
        with self.assertRaisesRegex(SystemOneError, "urgency: expected a score answer"):
            await self.evaluate(lambda request: httpx2.Response(200, json=payload))

    async def test_http_errors_surface_as_system_one_errors(self):
        with self.assertRaisesRegex(SystemOneError, "TypeSafeInternalServerError"):
            await self.evaluate(lambda request: httpx2.Response(500, json={"detail": "boom"}))
        with self.assertRaisesRegex(SystemOneError, "TypeSafeAuthenticationError"):
            await self.evaluate(lambda request: httpx2.Response(401, json={"detail": "bad key"}))


if __name__ == "__main__":
    unittest.main()
