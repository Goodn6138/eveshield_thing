ZX303 Tracker Backend
Simple Flask backend for ZX303 GPS tracker. Receives TCP data, parses binary packets, serves web dashboard.
Deploy to Render
Fork this repo or create new on GitHub
Connect repo to Render
Create Web Service with:
Build: pip install -r requirements.txt
Start: python zx303_backend.py
Add environment variable: PORT=10000
Deploy
Configure ZX303
Send SMS to tracker:
plain
adminip123456 your-render-url.com 5001
Or via serial:
plain
AT+SERVER=1,"your-render-url.com",5001,0
Endpoints
Table
URL	Description
/	Dashboard with map links
/api/devices	All devices JSON
/api/devices/<imei>	Single device JSON
/api/raw	Recent raw hex packets
