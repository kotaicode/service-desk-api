"""Phase 3+: Jira helpers live in `service_desk_crew.tools.jira`; re-export for compatibility."""
from service_desk_crew.tools.jira import jira_get_issue, jira_post_comment

__all__ = ["jira_get_issue", "jira_post_comment"]
