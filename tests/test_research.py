import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from charlie.research import web_search, read_url, get_search_urls, deep_research

@pytest.mark.asyncio
async def test_web_search_ddg():
    with patch("charlie.research.config.searxng_url", ""):
        with patch("charlie.research.DDGS") as mock_ddgs:
            mock_instance = mock_ddgs.return_value.__enter__.return_value
            mock_instance.text.return_value = [
                {"title": "Test Result", "body": "Snippet content", "href": "http://test.com"}
            ]
            
            result = await web_search("test query")
            assert "[WEB] Test Result: Snippet content (http://test.com)" in result

@pytest.mark.asyncio
async def test_get_search_urls():
    with patch("charlie.research.DDGS") as mock_ddgs:
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.text.return_value = [
            {"href": "http://test1.com"},
            {"href": "http://test2.com"}
        ]
        
        urls = await get_search_urls("test query", max_results=2)
        assert urls == ["http://test1.com", "http://test2.com"]

@pytest.mark.asyncio
async def test_read_url_fallback():
    # Force crawl4ai to fail to test fallback
    with patch("charlie.research.CRAWL4AI_AVAILABLE", False):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body><main>Test content</main></body></html>"
            mock_resp.headers = {"content-type": "text/html"}
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp
            
            result = await read_url("http://example.com")
            # markdownify result should be "Test content"
            assert "Test content" in result

@pytest.mark.asyncio
async def test_deep_research_flow():
    mock_brain = MagicMock()
    mock_brain.config.llm_model = "test-model"
    mock_brain.client.post = AsyncMock()
    
    # Mock decomposition and synthesis
    mock_resp_decomp = MagicMock(status_code=200)
    mock_resp_decomp.json.return_value = {"choices": [{"message": {"content": "Q1\nQ2\nQ3"}}]}
    mock_resp_synth = MagicMock(status_code=200)
    mock_resp_synth.json.return_value = {"choices": [{"message": {"content": "# Research Report\nFindings..."}}]}
    
    mock_brain.fast_client.post = AsyncMock(side_effect=[mock_resp_decomp, mock_resp_synth])
    
    with patch("charlie.research.get_search_urls", return_value=["http://test.com"]):
        with patch("charlie.research.read_url", return_value="Some content"):
            with patch("charlie.research.research_memory.create_session", return_value=1):
                with patch("charlie.research.research_memory.add_snippet"):
                    result = await deep_research("test topic", mock_brain)
                    assert "# Research Report" in result
                    assert "Findings..." in result
