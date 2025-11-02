from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from email.message import EmailMessage
import smtplib

app = FastAPI()

# Replace with your real credentials
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
FROM_EMAIL = "your_email@gmail.com"
FROM_PASSWORD = "your_app_password"

# Define request schema
class EmailRequest(BaseModel):
    email: EmailStr
    patient_id: str
    transcript: str

# Generate HTML content for email
def generate_email_content(patient_id: str, transcript: str) -> str:
    return f"""
    <html>
        <body>
            <h2>Patient Report</h2>
            <p><strong>Patient ID:</strong> {patient_id}</p>
            <hr>
            <h3>Transcript:</h3>
            <pre style="white-space: pre-wrap; font-family: monospace; background-color: #f4f4f4; padding: 10px;">{transcript}</pre>
            <p>Thank you.</p>
        </body>
    </html>
    """

# Send the email
def send_email(to_email: str, subject: str, html_content: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.set_content("Your email client does not support HTML.")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(FROM_EMAIL, FROM_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {e}")

# API endpoint
@app.post("/send-patient-email")
def send_patient_email(request: EmailRequest):
    html_body = generate_email_content(request.patient_id, request.transcript)
    subject = f"Transcript for Patient ID: {request.patient_id}"
    send_email(request.email, subject, html_body)
    return {"message": f"Email sent to {request.email}"}
