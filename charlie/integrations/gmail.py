import base64
import logging
import os
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from charlie.integrations.base import BaseIntegration

logger = logging.getLogger("charlie.integrations.gmail")

# If modifying these scopes, delete the file token_gmail.json.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]

class GmailIntegration(BaseIntegration):
    """
    GmailIntegration: Fetches recent emails via OAuth and sends emails.
    """
    def __init__(self):
        super().__init__("Gmail")
        self.creds = None
        self.service = None
        # Secure directory for tokens
        self.secure_dir = os.path.join(os.getcwd(), "config", "secure")
        if not os.path.exists(self.secure_dir):
            os.makedirs(self.secure_dir)

        self.token_path = os.path.join(self.secure_dir, "token_gmail.json")
        self.credentials_path = os.path.join(self.secure_dir, "credentials.json")

    def connect(self) -> bool:
        """Establishes connection using local credentials/token."""
        try:
            if os.path.exists(self.token_path):
                with open(self.token_path, 'r') as token:
                    self.creds = Credentials.from_json(token.read())

            # If there are no (valid) credentials available, let the user log in.
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    if not os.path.exists(self.credentials_path):
                        logger.error(f"gmail | credentials_missing | path={self.credentials_path}")
                        return False
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                    self.creds = flow.run_local_server(port=0, open_browser=False)

                # Save the credentials for the next run
                os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
                with open(self.token_path, 'w') as token:
                    token.write(self.creds.to_json())
                os.chmod(self.token_path, 0o600)

            self.service = build('gmail', 'v1', credentials=self.creds)
            return True
        except Exception as e:
            logger.error(f"gmail | connect_failed | {e}")
            return False

    def fetch(self, max_results: int = 10, query: str = "is:unread") -> list:
        """Retrieves messages from Gmail based on query. Uses batchGet to minimize API calls."""
        if not self.service:
            if not self.connect(): return []

        try:
            results = self.service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
            messages = results.get('messages', [])
            if not messages:
                return []

            # Batch fetch all messages in a single API call
            msg_ids = [m['id'] for m in messages]
            batch_results = self.service.users().messages().batchGet(
                userId='me', ids=msg_ids, format='metadata',
                metadataHeaders=['Subject', 'From']
            ).execute()

            clean_msgs = []
            for msg in batch_results.get('messages', []):
                headers = msg.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')

                clean_msgs.append({
                    "id": msg['id'],
                    "subject": subject,
                    "from": sender,
                    "snippet": msg.get('snippet', ''),
                    "source": "gmail"
                })
            return clean_msgs
        except Exception as e:
            logger.error(f"gmail | fetch_failed | {e}")
            return []

    def execute(self, action: str, **kwargs) -> bool:
        """Execute write actions for Gmail."""
        if not self.service:
            if not self.connect(): return False

        try:
            if action == "send_email":
                to = kwargs.get("to")
                subject = kwargs.get("subject", "No Subject")
                body = kwargs.get("body", "")
                if not to:
                    logger.error("gmail | send_email_failed | missing_to")
                    return False

                message = EmailMessage()
                message.set_content(body)
                message['To'] = to
                message['From'] = 'me'
                message['Subject'] = subject

                encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                create_message = {'raw': encoded_message}
                self.service.users().messages().send(userId="me", body=create_message).execute()
                logger.info(f"gmail | sent_email | to={to}")
                return True
            else:
                logger.warning(f"gmail | execute_unknown_action | action={action}")
                return False
        except Exception as e:
            logger.error(f"gmail | execute_failed | {e}")
            return False

    def disconnect(self):
        self.service = None
        self.creds = None
