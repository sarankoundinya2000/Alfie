# Alfie - Your Intelligent Meeting Assistant 🤖

Alfie is a smart meeting assistant that helps you schedule meetings, manage your calendar, and stay organized. It uses natural language processing to understand your requests and integrates with Google Calendar and Contacts to make scheduling meetings a breeze.

## Features

- 📅 Schedule meetings using natural language
- 🔍 Search contacts to find email addresses automatically
- 📧 Send email notifications for scheduled meetings
- 👥 View and manage your calendar events
- 🔐 Secure Google authentication
- 💬 User-friendly chat interface

## Prerequisites

- Python 3.7 or higher
- Google Cloud Project with Calendar API and People API enabled
- Groq API key

## Setup

1. Clone this repository:
```bash
git clone <repository-url>
cd alfie-meeting
```

2. Install the required packages:
```bash
pip install -r requirements.txt
```

3. Set up Google Cloud Project:
   - Go to the [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Enable the Google Calendar API and People API
   - Create OAuth 2.0 credentials
   - Download the credentials and save them as `credentials.json` in the project root

4. Set up Groq API:
   - Get your API key from [Groq](https://console.groq.com/)
   - Create a file named `groqapi.env` in the project root with:
   ```
   GROQ_API_KEY=your_api_key_here
   ```

## Running the Application

1. Start the application:
```bash
streamlit run app.py
```

2. Open your web browser and navigate to the URL shown in the terminal (typically http://localhost:8501)

3. Sign in with your Google account when prompted

## Usage

1. After signing in, you can interact with Alfie using natural language. Try commands like:
   - "Schedule a meeting with John tomorrow at 2 PM"
   - "What meetings do I have today?"
   - "Book a meeting with Sarah next Monday at 10 AM"

2. If you mention a person's name without an email, Alfie will search your Google Contacts to find their email address.

3. Once meeting details are confirmed, Alfie will:
   - Create a calendar event
   - Send email notifications to all participants
   - Show you a confirmation message

## Security

- All authentication is handled through Google OAuth
- Your credentials are stored securely
- API keys are kept in environment variables
- No sensitive data is stored in the application

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 