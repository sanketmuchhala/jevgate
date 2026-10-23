import json
import unittest
import httpx
from src.schemas import Proposition, ReviewRequest
from src.system_two import HttpSystemTwo, SystemTwoError

REQUEST = ReviewRequest(
    document="Release the first payment on Friday.",
    propositions=[Proposition(id="financial_commitment", text="Commits funds.", stakes="standard", jev_probability=0.55)],
)


class HttpSystemTwoTests(unittest.IsolatedAsyncioTestCase):
    async def review(self, handler):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await HttpSystemTwo(http, "http://system2.test/", timeout_s=5).review(REQUEST)

    async def test_posts_the_contract_and_parses_verdicts(self):
        sent = []

        def handler(request):
            sent.append(request)
            return httpx.Response(200, json={"model": "anthropic/claude-sonnet-5.5", "verdicts": [
                {"id": "financial_commitment", "verdict": "no", "evidence": "", "rationale": "No funds."},
            ]})

        resp = await self.review(handler)
        self.assertEqual(str(sent[0].url), "http://system2.test/v1/review")
        self.assertEqual(json.loads(sent[0].content), REQUEST.model_dump())
        self.assertEqual(resp.verdicts[0].verdict, "no")

    async def test_errors_and_off_contract_bodies_raise(self):
        with self.assertRaisesRegex(SystemTwoError, "HTTP 502"):
            await self.review(lambda r: httpx.Response(502, json={"error": "upstream"}))
        with self.assertRaisesRegex(SystemTwoError, "ValidationError"):
            await self.review(lambda r: httpx.Response(200, json={"verdicts": [{"id": "x", "verdict": "maybe"}]}))
        with self.assertRaisesRegex(SystemTwoError, "JSONDecodeError"):
            await self.review(lambda r: httpx.Response(200, text="<html>"))


if __name__ == "__main__":
    unittest.main()
