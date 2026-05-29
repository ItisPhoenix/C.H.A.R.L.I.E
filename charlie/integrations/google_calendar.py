import datetime
import logging
import os

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from charlie.integrations.base import BaseIntegration

# ── City geocode cache ─────────────────────────────────────────────────────────
_GEO_CITY_CACHE = {
    "tokyo": (35.6762, 139.6503), "new york": (40.7128, -74.0060),
    "london": (51.5074, -0.1278), "sydney": (-33.8688, 151.2093),
    "moscow": (55.7558, 37.6173), "dubai": (25.2048, 55.2708),
    "singapore": (1.3521, 103.8198), "mumbai": (19.0760, 72.8777),
    "cairo": (30.0444, 31.2357), "sao paulo": (-23.5505, -46.6333),
    "buenos aires": (-34.6037, -58.3816), "beijing": (39.9042, 116.4074),
    "paris": (48.8566, 2.3522), "berlin": (52.52, 13.405),
    "los angeles": (34.0522, -118.2437), "toronto": (43.6532, -79.3832),
    "mexico city": (19.4326, -99.1332), "seoul": (37.5665, 126.9780),
    "lagos": (6.5244, 3.3792), "chicago": (41.8781, -87.6298),
    "hong kong": (22.3193, 114.1694), "san francisco": (37.7749, -122.4194),
    "shanghai": (31.2304, 121.4737), "delhi": (28.7041, 77.1025),
    "bangkok": (13.7563, 100.5018), "jakarta": (-6.2088, 106.8456),
    "manila": (14.5995, 120.9842), "nairobi": (-1.2921, 36.8219),
    "johannesburg": (-26.2041, 28.0473), "istanbul": (41.0082, 28.9784),
}


def _geocode_location(location: str) -> tuple:
    """Return (lat, lng) for a location string, or (None, None) if unknown."""
    if not location:
        return None, None
    normalized = location.lower().strip()
    if normalized in _GEO_CITY_CACHE:
        return _GEO_CITY_CACHE[normalized]
    for city, coords in _GEO_CITY_CACHE.items():
        if city in normalized or normalized in city:
            return coords
    return None, None

logger = logging.getLogger("charlie.integrations.google_cal")

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.events', 'https://www.googleapis.com/auth/calendar.readonly']

class GoogleCalendarIntegration(BaseIntegration):
    """
    GoogleCalendarIntegration: Fetches events from Google Calendar via OAuth.
    """
    def __init__(self):
        super().__init__("Google Calendar")
        self.creds = None
        self.service = None
        # Secure directory for tokens
        self.secure_dir = os.path.join(os.getcwd(), "config", "secure")
        if not os.path.exists(self.secure_dir):
            os.makedirs(self.secure_dir)

        self.token_path = os.path.join(self.secure_dir, "token.json")
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
                        logger.error(f"google_cal | credentials_missing | path={self.credentials_path}")
                        return False
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                    self.creds = flow.run_local_server(port=0, open_browser=False)

                # Save the credentials for the next run
                os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
                with open(self.token_path, 'w') as token:
                    token.write(self.creds.to_json())
                os.chmod(self.token_path, 0o600)

            self.service = build('calendar', 'v3', credentials=self.creds)
            return True
        except Exception as e:
            logger.error(f"google_cal | connect_failed | {e}")
            return False

    def fetch(self, max_results: int = 10) -> list:
        """Retrieves upcoming events from the primary calendar."""
        if not self.service:
            if not self.connect(): return []

        try:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            events_result = self.service.events().list(
                calendarId='primary', timeMin=now,
                maxResults=max_results, singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            clean_events = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                location = event.get('location', '')
                lat, lng = _geocode_location(location)
                clean_events.append({
                    "summary": event.get('summary', 'No Title'),
                    "start": start,
                    "link": event.get('htmlLink'),
                    "id": event.get('id'),
                    "source": "google",
                    "location": location,
                    "lat": lat,
                    "lng": lng,
                })
            return clean_events
        except Exception as e:
            logger.error(f"google_cal | fetch_failed | {e}")
            return []

    def execute(self, action: str, **kwargs) -> bool:
        """Executes full duplex actions on the calendar."""
        if not self.service:
            if not self.connect(): return False

        try:
            if action == "create_event":
                summary = kwargs.get("summary", "New Event")
                start_time = kwargs.get("start_time")
                end_time = kwargs.get("end_time")
                if not start_time or not end_time:
                    logger.error("google_cal | create_event | missing start_time or end_time")
                    return False

                event = {
                  'summary': summary,
                  'start': {
                    'dateTime': start_time,
                    'timeZone': 'UTC',
                  },
                  'end': {
                    'dateTime': end_time,
                    'timeZone': 'UTC',
                  },
                }
                if "description" in kwargs:
                    event['description'] = kwargs["description"]

                event = self.service.events().insert(calendarId='primary', body=event).execute()
                logger.info(f"google_cal | event_created | link={event.get('htmlLink')}")
                return True

            elif action == "delete_event":
                event_id = kwargs.get("event_id")
                if not event_id: return False
                self.service.events().delete(calendarId='primary', eventId=event_id).execute()
                logger.info(f"google_cal | event_deleted | id={event_id}")
                return True

            else:
                logger.warning(f"google_cal | execute_unknown_action | action={action}")
                return False

        except Exception as e:
            logger.error(f"google_cal | execute_failed | action={action} | {e}")
            return False

    def disconnect(self):
        self.service = None
        self.creds = None
