import logging
import os

from notion_client import Client

from charlie.integrations.base import BaseIntegration

logger = logging.getLogger("charlie.integrations.notion")

class NotionIntegration(BaseIntegration):
    """
    Handles Notion API interaction.
    Requires NOTION_TOKEN environment variable.
    """

    def __init__(self):
        super().__init__("Notion")
        self.client = None
        self.token = os.getenv("NOTION_TOKEN")

    def connect(self) -> bool:
        if not self.token:
            logger.error("notion_auth_failed | NOTION_TOKEN missing")
            return False
        try:
            self.client = Client(auth=self.token)
            # Verify connection by fetching bot user info
            self.client.users.me()
            logger.info("notion_connected")
            return True
        except Exception as e:
            logger.error(f"notion_connect_error | {e}")
            return False

    def fetch(self, **kwargs) -> list:
        """
        Fetches pages from the workspace.
        kwargs:
            - limit (int): Max pages to fetch.
        """
        if not self.client and not self.connect():
            return []

        limit = kwargs.get("limit", 10)
        try:
            # Simple search for recent pages
            results = self.client.search(
                filter={"property": "object", "value": "page"},
                sort={"direction": "descending", "timestamp": "last_edited_time"},
                page_size=limit
            ).get("results", [])

            pages = []
            for page in results:
                title = "Untitled"
                # Notion properties are dynamic, try to find a title-like property
                props = page.get("properties", {})
                for p_name, p_val in props.items():
                    if p_val.get("type") == "title":
                        title_list = p_val.get("title", [])
                        if title_list:
                            title = title_list[0].get("plain_text", "Untitled")
                        break

                pages.append({
                    "id": page.get("id"),
                    "title": title,
                    "url": page.get("url"),
                    "last_edited": page.get("last_edited_time")
                })
            return pages
        except Exception as e:
            logger.error(f"notion_fetch_error | {e}")
            return []

    def execute(self, action: str, **kwargs) -> bool:
        """Execute write actions for Notion."""
        if not self.client and not self.connect():
            return False

        try:
            if action == "create_page":
                parent_page_id = kwargs.get("parent_page_id")
                title = kwargs.get("title", "New Page")
                content = kwargs.get("content", "")

                if not parent_page_id:
                    logger.error("notion_create_page | missing_parent_page_id")
                    return False

                new_page = {
                    "parent": {"page_id": parent_page_id},
                    "properties": {
                        "title": {
                            "title": [{"text": {"content": title}}]
                        }
                    }
                }
                if content:
                    new_page["children"] = [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": content}}]
                            }
                        }
                    ]
                self.client.pages.create(**new_page)
                logger.info(f"notion_page_created | title={title}")
                return True
            else:
                logger.warning(f"notion_execute_unknown | action={action}")
                return False
        except Exception as e:
            logger.error(f"notion_execute_error | {e}")
            return False

    def disconnect(self):
        self.client = None
