import os
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle


class Emailer:
    """
    Gmail API email sender class.
    Handles authentication and sending emails with optional attachments.
    """

    SCOPES = ['https://www.googleapis.com/auth/gmail.send']

    def __init__(self, credentials_path='credentials.json', token_path='token.pickle'):
        """
        Initialize Emailer with paths to credentials and token files.

        Args:
            credentials_path: Path to Google OAuth credentials JSON file
            token_path: Path to store/load authentication token
        """
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None

    def authenticate(self):
        """
        Authenticate and initialize Gmail API service.
        First run will open browser for OAuth consent.

        Returns:
            Gmail API service object
        """
        creds = None

        # Load existing token if available
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                creds = pickle.load(token)

        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)

            # Save credentials for next run
            with open(self.token_path, 'wb') as token:
                pickle.dump(creds, token)

        self.service = build('gmail', 'v1', credentials=creds)
        return self.service

    def create_message(self, sender, to, subject, body, attachment_path=None):
        """
        Create email message.

        Args:
            sender: Sender email address
            to: Recipient email address
            subject: Email subject
            body: Email body text
            attachment_path: Optional path to file attachment

        Returns:
            Message object ready to send
        """
        message = MIMEMultipart()
        message['to'] = to
        message['from'] = sender
        message['subject'] = subject

        # Add body
        msg = MIMEText(body)
        message.attach(msg)

        # Add attachment if provided
        if attachment_path:
            try:
                content_type = 'application/octet-stream'

                with open(attachment_path, 'rb') as f:
                    attachment_data = f.read()

                part = MIMEBase(*content_type.split('/'))
                part.set_payload(attachment_data)
                encoders.encode_base64(part)

                filename = os.path.basename(attachment_path)
                part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                message.attach(part)

            except Exception as e:
                print(f"Error attaching file: {e}")

        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return {'raw': raw_message}

    def send_email(self, sender, to, subject, body, attachment_path=None):
        """
        Send email using Gmail API.

        Args:
            sender: Sender email address
            to: Recipient email address
            subject: Email subject
            body: Email body text
            attachment_path: Optional path to file attachment

        Returns:
            Sent message object or None if failed
        """
        if not self.service:
            self.authenticate()

        message = self.create_message(sender, to, subject, body, attachment_path)
        sent_message = self.service.users().messages().send(
            userId='me',
            body=message
        ).execute()

        print(f"Email sent successfully!")
        print(f"Message ID: {sent_message['id']}")
        return sent_message

    def send_html_email(self, sender, to, subject, html_body):
        """
        Send HTML formatted email.

        Args:
            sender: Sender email address
            to: Recipient email address
            subject: Email subject
            html_body: HTML formatted email body

        Returns:
            Sent message object or None if failed
        """
        if not self.service:
            self.authenticate()

        try:
            message = MIMEMultipart('alternative')
            message['to'] = to
            message['from'] = sender
            message['subject'] = subject

            html_part = MIMEText(html_body, 'html')
            message.attach(html_part)

            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            sent_message = self.service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()

            print(f"HTML email sent successfully!")
            print(f"Message ID: {sent_message['id']}")
            return sent_message

        except Exception as e:
            print(f"An error occurred: {e}")
            return None


# Example usage
if __name__ == "__main__":
    # Initialize emailer
    emailer = Emailer()

    # Authenticate (only needed first time or when token expires)
    print("Authenticating with Gmail API...")
    emailer.authenticate()

    # Email details
    SENDER_EMAIL = "theexperimentedsoldier@gmail.com"
    RECIPIENT_EMAIL = "aaronvm9@gmail.com"
    SUBJECT = "Test Email from Gmail API"
    BODY = """Hello!

It's GOONING TIME

Best jakol,
ako lang to"""

    # Send simple email
    emailer.send_email(SENDER_EMAIL, RECIPIENT_EMAIL, SUBJECT, BODY)

    # Send email with attachment
    # emailer.send_email(SENDER_EMAIL, RECIPIENT_EMAIL, SUBJECT, BODY, "IMG_2960.PNG")

    # Send HTML email
    # html_content = """
    # <html>
    #     <body>
    #         <h1>Hello!</h1>
    #         <p>This is an <strong>HTML email</strong> sent using the Gmail API.</p>
    #     </body>
    # </html>
    # """
    # emailer.send_html_email(SENDER_EMAIL, RECIPIENT_EMAIL, "HTML Test", html_content)