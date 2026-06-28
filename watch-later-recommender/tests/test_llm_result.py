"""Quick smoke test for LLM result parsing after removing fallback."""
import json

from watch_later_recommender.models import VideoItem
from watch_later_recommender.recommender import _extract_json, parse_llm_result


def test_extract_from_fence():
    text = """为您精选了5个视频：

以下是推荐总结...

```json
{"bvids": ["BV001", "BV002"], "reasons": ["理由1", "理由2"], "surprise_count": 0}
```"""
    json_str = _extract_json(text)
    assert json_str is not None
    data = json.loads(json_str)
    assert data["bvids"] == ["BV001", "BV002"]


def test_extract_raw_json():
    text = '{"bvids": ["BV003"], "reasons": ["理由"], "surprise_count": 1}'
    json_str = _extract_json(text)
    assert json_str is not None


def test_parse_llm_result_valid():
    text = """推荐总结...

```json
{"bvids": ["BV001", "BV002"], "reasons": ["理由1", "理由2"], "surprise_count": 1}
```"""
    candidates = [
        VideoItem(aid=1, bvid="BV001", title="t1", tid=36, tname="知识", duration=300,
                  owner_name="UP1", view=1000, like=100),
        VideoItem(aid=2, bvid="BV002", title="t2", tid=188, tname="数码", duration=600,
                  owner_name="UP2", view=2000, like=200),
    ]
    result = parse_llm_result(text, candidates, count=2)
    assert result is not None
    assert result.bvids == ["BV001", "BV002"]
    assert result.reasons == ["理由1", "理由2"]
    assert result.surprise_count == 1


def test_parse_llm_result_invalid_bvid():
    text = '{"bvids": ["BV999"], "reasons": ["理由"], "surprise_count": 0}'
    candidates = [
        VideoItem(aid=1, bvid="BV001", title="t1", tid=36, tname="知识", duration=300,
                  owner_name="UP1", view=1000, like=100),
    ]
    result = parse_llm_result(text, candidates, count=1)
    assert result is None  # BV999 not in candidate pool
