import smtplib
from email.message import EmailMessage
import os
from datetime import datetime
import pytz
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from app.core.supabase import supabase

class ReportEngine:
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SENDER_EMAIL = "smartgrill2026@gmail.com"
    # Note: Replace with a 16-character Google App Password
    SENDER_PASSWORD = "YOUR_GMAIL_APP_PASSWORD" 
    
    @staticmethod
    def generate_and_email_shift_report(shift: str, business_date: str):
        try:
            res = supabase.table("sales").select("*").eq("shift", shift).eq("business_date", business_date).execute()
            sales = res.data or []
            
            total_cash = sum(s.get('cash_amount', 0) for s in sales)
            total_mpesa = sum(s.get('mpesa_amount', 0) for s in sales)
            grand_total = total_cash + total_mpesa
            
            pdf_path = f"/tmp/report_{business_date}_{shift}.pdf"
            c = canvas.Canvas(pdf_path, pagesize=letter)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(50, 750, "SMART GRILL - END OF SHIFT SALES REPORT")
            
            c.setFont("Helvetica", 12)
            c.drawString(50, 720, f"Shift: {shift}")
            c.drawString(50, 700, f"Business Date: {business_date}")
            
            tz = pytz.timezone('Africa/Nairobi')
            c.drawString(50, 680, f"Generated On: {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} EAT")
            c.drawString(50, 650, f"Total Transactions: {len(sales)}")
            c.drawString(50, 630, f"Cash Collected: KSh {total_cash}")
            c.drawString(50, 610, f"M-Pesa Collected: KSh {total_mpesa}")
            
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, 580, f"GRAND TOTAL: KSh {grand_total}")
            c.save()
            
            msg = EmailMessage()
            msg['Subject'] = f"Automated Shift Report: {business_date} {shift}"
            msg['From'] = ReportEngine.SENDER_EMAIL
            msg['To'] = "smartgrill2026@gmail.com"
            msg.set_content(f"Hello Admin,\n\nAttached is the automated end-of-shift sales report for the {shift} shift on {business_date}.\n\nTotal Sales: KSh {grand_total}\n\nSmart Grill POS System")
            
            with open(pdf_path, 'rb') as f:
                pdf_data = f.read()
            msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=f"{business_date}_{shift}_report.pdf")
            
            with smtplib.SMTP_SSL(ReportEngine.SMTP_SERVER, ReportEngine.SMTP_PORT) as server:
                server.login(ReportEngine.SENDER_EMAIL, ReportEngine.SENDER_PASSWORD)
                server.send_message(msg)
                
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                
        except Exception as e:
            print(f"Failed to send shift report email: {e}")