import logging
import os

from github import Github

from charlie.integrations.base import BaseIntegration

logger = logging.getLogger("charlie.integrations.github")

class GitHubIntegration(BaseIntegration):
    """
    GitHubIntegration: Interacts with GitHub API via PyGithub.
    Supports repository listing, issue fetching, and activity tracking.
    Requires GITHUB_TOKEN environment variable.
    """
    def __init__(self):
        super().__init__("GitHub")
        self.client = None
        self.token = os.getenv("GITHUB_TOKEN")

    def connect(self) -> bool:
        """Authenticates with GitHub using personal access token."""
        if not self.token:
            logger.error("github | token_missing | Ensure GITHUB_TOKEN is set in environment.")
            return False
        try:
            self.client = Github(self.token)
            # Verify connectivity by fetching user info
            user = self.client.get_user()
            logger.info(f"github | connected | user={user.login}")
            return True
        except Exception as e:
            logger.error(f"github | connect_failed | {e}")
            return False

    def fetch(self, repo_name: str = None, limit: int = 10) -> list:
        """
        Retrieves repository data or issues.
        If repo_name is provided, fetches open issues.
        Otherwise, fetches recent active repositories for the user.
        """
        if not self.client:
            if not self.connect(): return []

        try:
            if repo_name == "alerts":
                # Fetch ambient alerts: assigned issues and PRs requesting review
                user = self.client.get_user()
                login = user.login
                alerts = []
                # PRs requesting review
                try:
                    pr_search = self.client.search_issues(f"is:open is:pr review-requested:{login}")
                    for pr in pr_search[:limit]:
                        alerts.append({
                            "type": "pr_review",
                            "title": pr.title,
                            "url": pr.html_url,
                            "repo": pr.repository.full_name,
                            "source": "github_alert"
                        })
                except Exception as e:
                    logger.debug(f"github | pr_search_failed | {e}")

                # Assigned issues
                try:
                    issues = user.get_issues(filter="assigned", state="open")
                    for issue in issues[:limit]:
                        alerts.append({
                            "type": "assigned_issue",
                            "title": issue.title,
                            "url": issue.html_url,
                            "repo": issue.repository.full_name,
                            "source": "github_alert"
                        })
                except Exception as e:
                    logger.debug(f"github | issue_search_failed | {e}")
                return alerts

            elif repo_name:
                repo = self.client.get_repo(repo_name)
                issues = repo.get_issues(state='open')[:limit]
                return [{
                    "id": i.id,
                    "number": i.number,
                    "title": i.title,
                    "url": i.html_url,
                    "author": i.user.login,
                    "source": "github_issue"
                } for i in issues]
            else:
                user = self.client.get_user()
                repos = user.get_repos(sort='updated', direction='desc')[:limit]
                return [{
                    "name": r.full_name,
                    "url": r.html_url,
                    "description": r.description,
                    "updated": r.updated_at.isoformat(),
                    "source": "github_repo"
                } for r in repos]
        except Exception as e:
            logger.error(f"github | fetch_failed | {e}")
            return []

    def execute(self, action: str, **kwargs) -> str:
        """GitHub write actions are not yet implemented. Read-only fetch is available."""
        logger.warning(f"github | execute_not_implemented | action={action}")
        return "GitHub write actions are not yet implemented. Read-only fetch is available."

    def disconnect(self):
        self.client = None
