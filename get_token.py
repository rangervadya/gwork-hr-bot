# get_token.py
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=8080, open_browser=True)

# Сохраняем токен для вашего Telegram ID (1482161996)
with open('token_1482161996.pickle', 'wb') as token:
    pickle.dump(creds, token)

print("✅ Токен сохранён! Теперь можно запускать бота.")