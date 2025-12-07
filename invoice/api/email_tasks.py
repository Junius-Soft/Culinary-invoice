"""
Otomatik email sync için scheduled tasks
"""

import frappe

def sync_gmail_invoices():
    """
    Her 5 dakikada bir Gmail'den email'leri çeker
    Cron: */5 * * * * (her 5 dakika)
    """
    try:
        print(f">>>>>> [SCHEDULER] Email sync başlatılıyor... {frappe.utils.now()}")
        
        # Tüm aktif Email Account'ları al
        email_accounts = frappe.get_all("Email Account",
            filters={
                "enable_incoming": 1  # Sadece gelen email aktif olan hesapları al
            },
            fields=["name", "email_id"]
        )
        
        if not email_accounts:
            print(">>>>>> [SCHEDULER] Aktif Email Account bulunamadı")
            return
        
        # Her hesap için email çek
        for account in email_accounts:
            try:
                print(f">>>>>> [SCHEDULER] Email çekiliyor: {account.email_id} ({account.name})")
                
                email_doc = frappe.get_doc("Email Account", account.name)
                
                # Email'leri çek
                email_doc.receive()
                
                print(f"✅ [SCHEDULER] {account.email_id} için email'ler çekildi")
                
            except Exception as e:
                print(f"❌ [SCHEDULER] {account.name} hatası: {str(e)}")
                frappe.log_error(
                    title=f"Email Sync Error - {account.name}",
                    message=str(e)
                )
        
        frappe.db.commit()
        print(f">>>>>> [SCHEDULER] Email sync tamamlandı! {frappe.utils.now()}")
        
    except Exception as e:
        print(f"❌ [SCHEDULER] Genel hata: {str(e)}")
        frappe.log_error(
            title="Scheduler Email Sync Error",
            message=str(e)
        )
