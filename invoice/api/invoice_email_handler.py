import frappe
import re
from datetime import datetime

logger = frappe.logger("invoice.email_handler", allow_site=frappe.local.site)

def process_invoice_email(doc, method=None):
    """Communication DocType'ına gelen email'leri yakala ve fatura oluştur"""
    print(f"[INVOICE] Email işleme başladı: {doc.subject} (Communication: {doc.name})")
    logger.info(f"Email işleme başladı: {doc.subject} (Communication: {doc.name})")
    
    stats = {
        "total_detected": 0,
        "already_processed": 0,
        "newly_processed": 0,
        "errors": 0,
        "invoices_created": []
    }
    
    try:
        if doc.communication_type != "Communication" or doc.sent_or_received != "Received":
            print(f"[INVOICE] Email atlandı - type: {doc.communication_type}, received: {doc.sent_or_received}")
            logger.info(f"Email atlandı - type: {doc.communication_type}, received: {doc.sent_or_received}")
            return
        
        # NOT: Duplicate kontrolü sadece invoice_number (Rechnungsnummer) ile yapılacak
        # Email seviyesinde kontrol kaldırıldı - aynı email'den farklı faturalar gelebilir
        
        attachments = frappe.get_all("File",
            filters={
                "attached_to_doctype": "Communication",
                "attached_to_name": doc.name,
            },
            fields=["name", "file_url", "file_name", "file_size"]
        )
        
        pdf_attachments = [
            att for att in attachments 
            if att.get('file_name') and att.get('file_name').lower().endswith('.pdf')
        ]
        
        subject = (doc.subject or "").lower()
        
        # ÖNEMLİ: "Ihre neue Aktivitätsübersicht" içeren email'ler UberEats faturaları
        is_uber_eats_report = "ihre neue aktivitätsübersicht" in subject
        if is_uber_eats_report:
            print(f"[INVOICE] ✅ UberEats Aktivitätsübersicht email'i tespit edildi: {doc.subject}")
            logger.info(f"UberEats Aktivitätsübersicht email'i tespit edildi: {doc.subject}")
            print(f"[INVOICE] Tüm PDF'ler taranacak ({len(pdf_attachments)} adet)")
            logger.info(f"Tüm PDF'ler taranacak ({len(pdf_attachments)} adet)")
            stats["total_detected"] = len(pdf_attachments)
            
            if not pdf_attachments:
                print(f"[INVOICE] ⚠️ UberEats email'inde PDF bulunamadı")
                logger.warning("UberEats email'inde PDF bulunamadı")
                stats["errors"] = 1
                show_summary_notification(stats, doc.subject)
                return
        
        # ÖNEMLİ: "Wolt payout report" içeren email'lerdeki tüm PDF'leri işle
        is_wolt_payout_report = "wolt payout report" in subject
        if is_wolt_payout_report:
            print(f"[INVOICE] ✅ Wolt payout report email'i tespit edildi: {doc.subject}")
            logger.info(f"Wolt payout report email'i tespit edildi: {doc.subject}")
            print(f"[INVOICE] Tüm PDF'ler taranacak ({len(pdf_attachments)} adet)")
            logger.info(f"Tüm PDF'ler taranacak ({len(pdf_attachments)} adet)")
            stats["total_detected"] = len(pdf_attachments)
            
            if not pdf_attachments:
                print(f"[INVOICE] ⚠️ Wolt payout report email'inde PDF bulunamadı")
                logger.warning("Wolt payout report email'inde PDF bulunamadı")
                stats["errors"] = 1
                show_summary_notification(stats, doc.subject)
                return
        
        # Normal fatura kontrolü - sadece özel email'ler değilse
        if not is_uber_eats_report and not is_wolt_payout_report:
            keywords = ["invoice", "fatura", "rechnung", "facture", "bill"]
            has_invoice_subject = any(keyword in subject for keyword in keywords)
            
            if not has_invoice_subject:
                print(f"[INVOICE] Email atlandı - fatura değil: {doc.subject}")
                logger.info(f"Email atlandı - fatura değil: {doc.subject}")
                return
            
            print(f"[INVOICE] ✅ Fatura email'i tespit edildi: {doc.subject}")
            logger.info(f"Fatura email'i tespit edildi: {doc.subject}")
            stats["total_detected"] = 1
            
            if not pdf_attachments:
                stats["errors"] = 1
                show_summary_notification(stats, doc.subject)
                return
        
        for pdf in pdf_attachments:
            try:
                # UberEats email'lerinde: Sadece "Bestell- und Zahlungsübersicht" başlığı olan PDF'leri işle
                if is_uber_eats_report:
                    # PDF içeriğini hızlıca kontrol et
                    has_uber_eats_header = check_pdf_has_uber_eats_header(pdf)
                    if not has_uber_eats_header:
                        print(f"[INVOICE] ⏭️ PDF atlandı (Bestell- und Zahlungsübersicht yok): {pdf.file_name}")
                        logger.info(f"PDF atlandı (Bestell- und Zahlungsübersicht yok): {pdf.file_name}")
                        continue
                    print(f"[INVOICE] ✅ PDF işlenecek (Bestell- und Zahlungsübersicht bulundu): {pdf.file_name}")
                    logger.info(f"PDF işlenecek (Bestell- und Zahlungsübersicht bulundu): {pdf.file_name}")
                
                # Wolt payout report email'lerinde: Sadece "Rechnung(Selbstfakturierung)" başlığı olan PDF'leri işle
                if is_wolt_payout_report:
                    # PDF içeriğini hızlıca kontrol et - hem "Rechnung" hem "Selbstfakturierung" olmalı
                    has_selbstfakturierung = check_pdf_has_selbstfakturierung(pdf)
                    if not has_selbstfakturierung:
                        print(f"[INVOICE] ⏭️ PDF atlandı (Rechnung(Selbstfakturierung) yok): {pdf.file_name}")
                        logger.info(f"PDF atlandı (Rechnung(Selbstfakturierung) yok): {pdf.file_name}")
                        continue
                    print(f"[INVOICE] ✅ PDF işlenecek (Rechnung(Selbstfakturierung) bulundu): {pdf.file_name}")
                    logger.info(f"PDF işlenecek (Rechnung(Selbstfakturierung) bulundu): {pdf.file_name}")
                
                invoice = create_invoice_from_pdf(doc, pdf)
                if invoice:
                    stats["newly_processed"] += 1
                    stats["invoices_created"].append({
                        "doctype": invoice.doctype,
                        "name": invoice.name,
                        "invoice_number": getattr(invoice, "invoice_number", "N/A")
                    })
                else:
                    stats["already_processed"] += 1
            except Exception as e:
                stats["errors"] += 1
                frappe.log_error(
                    title="Invoice PDF Processing Error",
                    message=f"PDF: {pdf.file_name}\nError: {str(e)}\n{frappe.get_traceback()}"
                )
        
        frappe.db.commit()
        print(f"[INVOICE] Email işleme tamamlandı. Stats: {stats}")
        logger.info(f"Email işleme tamamlandı. Stats: {stats}")
        print(f"[INVOICE] Bildirim fonksiyonu çağrılıyor...")
        show_summary_notification(stats, doc.subject)
        print(f"[INVOICE] Bildirim fonksiyonu tamamlandı.")
        
    except Exception as e:
        print(f"[INVOICE] ❌ Email işleme hatası: {str(e)}")
        logger.error(f"Email işleme hatası: {str(e)}")
        frappe.log_error(
            title="Invoice Email Processing Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def create_invoice_from_pdf(communication_doc, pdf_attachment):
    """PDF'den Invoice kaydı oluştur"""
    file_name = pdf_attachment.get('file_name', '')
    print(f"[INVOICE] PDF işleniyor: {file_name}")
    logger.info(f"PDF işleniyor: {file_name}")
    
    # Dosya adına göre platform tespiti (öncelikli)
    file_name_lower = file_name.lower() if file_name else ''
    platform_from_filename = detect_platform_from_filename(file_name_lower)
    print(f"[INVOICE] Dosya adından platform: {platform_from_filename}")
    logger.info(f"Dosya adından platform: {platform_from_filename}")
    
    extracted_data = extract_invoice_data_from_pdf(pdf_attachment)
    
    # PDF içeriğinden platform tespiti
    platform_from_content = extracted_data.get("platform")
    print(f"[INVOICE] İçerikten platform: {platform_from_content}")
    logger.info(f"İçerikten platform: {platform_from_content}")
    
    # Dosya adı tespiti öncelikli, yoksa içerik tespiti
    platform = platform_from_filename or platform_from_content
    
    # ÖNEMLİ: Platform tespit edilemezse işleme (1&1, diğer faturalar gibi)
    if not platform or platform == "unknown":
        print(f"[INVOICE] ⚠️ Platform tespit edilemedi, email atlanıyor: {file_name}")
        logger.warning(f"Platform tespit edilemedi, email atlanıyor: {file_name}")
        return None
    
    print(f"[INVOICE] Seçilen platform: {platform}")
    logger.info(f"Seçilen platform: {platform}")
    
    if platform == "wolt":
        print(f"[INVOICE] ✅ Wolt Invoice oluşturuluyor")
        logger.info("Wolt Invoice oluşturuluyor")
        return create_wolt_invoice_doc(communication_doc, pdf_attachment, extracted_data)
    
    if platform == "uber_eats":
        print(f"[INVOICE] ✅ UberEats Invoice oluşturuluyor")
        logger.info("UberEats Invoice oluşturuluyor")
        return create_uber_eats_invoice_doc(communication_doc, pdf_attachment, extracted_data)
    
    print(f"[INVOICE] ✅ Lieferando Invoice oluşturuluyor")
    logger.info("Lieferando Invoice oluşturuluyor")
    return create_lieferando_invoice_doc(communication_doc, pdf_attachment, extracted_data)


def create_lieferando_invoice_doc(communication_doc, pdf_attachment, extracted_data):
    """Lieferando Invoice kaydı oluştur"""
    invoice_number = extracted_data.get("invoice_number")
    
    # Duplicate kontrolü: Sadece invoice_number (Rechnungsnummer) ile kontrol
    if invoice_number:
        existing_invoice = frappe.db.exists("Lieferando Invoice", {"invoice_number": invoice_number})
        if existing_invoice:
            print(f"[INVOICE] ⚠️ Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            logger.info(f"Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            return None
        print(f"[INVOICE] ✅ Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
        logger.info(f"Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
    else:
        print(f"[INVOICE] ⚠️ Invoice number bulunamadı, geçici numara kullanılacak")
        logger.warning("Invoice number bulunamadı, geçici numara kullanılacak")
    
    invoice = frappe.get_doc({
        "doctype": "Lieferando Invoice",
        "invoice_number": invoice_number or generate_temp_invoice_number(),
        "invoice_date": extracted_data.get("invoice_date") or frappe.utils.today(),
        "period_start": extracted_data.get("period_start"),
        "period_end": extracted_data.get("period_end"),
        "status": "Draft",
        "supplier_name": extracted_data.get("supplier_name") or "yd.yourdelivery GmbH",
        "supplier_email": extracted_data.get("supplier_email") or communication_doc.sender,
        "supplier_ust_idnr": extracted_data.get("supplier_ust_idnr"),
        "supplier_iban": extracted_data.get("supplier_iban"),
        "restaurant_name": extracted_data.get("restaurant_name"),
        "customer_number": extracted_data.get("customer_number"),
        "customer_company": extracted_data.get("customer_company"),
        "restaurant_address": extracted_data.get("restaurant_address"),
        "customer_bank_iban": extracted_data.get("customer_bank_iban"),
        "total_orders": extracted_data.get("total_orders") or 0,
        "total_revenue": extracted_data.get("total_revenue") or 0,
        "online_paid_orders": extracted_data.get("online_paid_orders") or 0,
        "online_paid_amount": extracted_data.get("online_paid_amount") or 0,
        "ausstehende_am_datum": extracted_data.get("invoice_date"),
        "ausstehende_onlinebezahlungen_betrag": extracted_data.get("outstanding_balance") or extracted_data.get("total_revenue") or 0,
        "rechnungsausgleich_betrag": extracted_data.get("total_amount") or 0,
        "auszahlung_gesamt": extracted_data.get("payout_amount") or 0,
        "service_fee_rate": extracted_data.get("service_fee_rate") or 30,
        "service_fee_amount": extracted_data.get("service_fee_amount") or 0,
        "admin_fee_amount": extracted_data.get("admin_fee_amount") or 0,
        "subtotal": extracted_data.get("subtotal") or 0,
        "tax_rate": extracted_data.get("tax_rate") or 19,
        "tax_amount": extracted_data.get("tax_amount") or 0,
        "total_amount": extracted_data.get("total_amount") or 0,
        "paid_online_payments": extracted_data.get("paid_online_payments") or 0,
        "outstanding_amount": extracted_data.get("outstanding_amount") or 0,
        "payout_amount": extracted_data.get("payout_amount") or 0,
        "outstanding_balance": extracted_data.get("outstanding_balance") or 0,
        "email_subject": communication_doc.subject,
        "email_from": communication_doc.sender,
        "received_date": communication_doc.creation,
        "processed_date": frappe.utils.now(),
        "extraction_confidence": extracted_data.get("confidence", 50),
        "raw_text": extracted_data.get("raw_text", "")
    })
    
    order_items = extracted_data.get("order_items", [])
    if order_items:
        invoice.order_items = order_items
    
    # name (ID) field'ını invoice_number (Rechnungsnummer) ile aynı yap
    final_invoice_number = invoice_number or generate_temp_invoice_number()
    invoice.name = final_invoice_number
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    attach_pdf_to_invoice(pdf_attachment, invoice.name, "Lieferando Invoice")
    notify_invoice_created("Lieferando Invoice", invoice.name, invoice.invoice_number, communication_doc.subject)
    
    return invoice


def create_wolt_invoice_doc(communication_doc, pdf_attachment, extracted_data):
    """Wolt Invoice kaydı oluştur"""
    invoice_number = extracted_data.get("invoice_number")
    
    # Duplicate kontrolü: Sadece invoice_number (Rechnungsnummer) ile kontrol
    if invoice_number:
        existing_invoice = frappe.db.exists("Wolt Invoice", {"invoice_number": invoice_number})
        if existing_invoice:
            print(f"[INVOICE] ⚠️ Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            logger.info(f"Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            return None
        print(f"[INVOICE] ✅ Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
        logger.info(f"Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
    else:
        print(f"[INVOICE] ⚠️ Invoice number bulunamadı, geçici numara kullanılacak")
        logger.warning("Invoice number bulunamadı, geçici numara kullanılacak")
    
    invoice = frappe.get_doc({
        "doctype": "Wolt Invoice",
        "invoice_number": invoice_number or generate_temp_invoice_number(),
        "invoice_date": extracted_data.get("invoice_date") or frappe.utils.today(),
        "period_start": extracted_data.get("period_start"),
        "period_end": extracted_data.get("period_end"),
        "status": "Draft",
        "supplier_name": extracted_data.get("supplier_name") or "Wolt Enterprises Deutschland GmbH",
        "supplier_vat": extracted_data.get("supplier_vat"),
        "supplier_address": extracted_data.get("supplier_address"),
        "restaurant_name": extracted_data.get("restaurant_name"),
        "customer_number": extracted_data.get("customer_number"),
        "restaurant_address": extracted_data.get("restaurant_address"),
        "goods_net_7": extracted_data.get("goods_net_7") or 0,
        "goods_vat_7": extracted_data.get("goods_vat_7") or 0,
        "goods_gross_7": extracted_data.get("goods_gross_7") or 0,
        "goods_net_19": extracted_data.get("goods_net_19") or 0,
        "goods_vat_19": extracted_data.get("goods_vat_19") or 0,
        "goods_gross_19": extracted_data.get("goods_gross_19") or 0,
        "goods_net_total": extracted_data.get("goods_net_total") or 0,
        "goods_vat_total": extracted_data.get("goods_vat_total") or 0,
        "goods_gross_total": extracted_data.get("goods_gross_total") or 0,
        "distribution_net_total": extracted_data.get("distribution_net_total") or 0,
        "distribution_vat_total": extracted_data.get("distribution_vat_total") or 0,
        "distribution_gross_total": extracted_data.get("distribution_gross_total") or 0,
        "netprice_net_7": extracted_data.get("netprice_net_7") or 0,
        "netprice_vat_7": extracted_data.get("netprice_vat_7") or 0,
        "netprice_gross_7": extracted_data.get("netprice_gross_7") or 0,
        "netprice_net_19": extracted_data.get("netprice_net_19") or 0,
        "netprice_vat_19": extracted_data.get("netprice_vat_19") or 0,
        "netprice_gross_19": extracted_data.get("netprice_gross_19") or 0,
        "netprice_net_total": extracted_data.get("netprice_net_total") or 0,
        "netprice_vat_total": extracted_data.get("netprice_vat_total") or 0,
        "netprice_gross_total": extracted_data.get("netprice_gross_total") or 0,
        "end_amount_net": extracted_data.get("end_amount_net") or 0,
        "end_amount_vat": extracted_data.get("end_amount_vat") or 0,
        "end_amount_gross": extracted_data.get("end_amount_gross") or 0,
        "email_subject": communication_doc.subject,
        "email_from": communication_doc.sender,
        "received_date": communication_doc.creation,
        "processed_date": frappe.utils.now(),
        "extraction_confidence": extracted_data.get("confidence", 55),
        "raw_text": extracted_data.get("raw_text", "")
    })
    
    # name (ID) field'ını invoice_number (Rechnungsnummer) ile aynı yap
    final_invoice_number = invoice_number or generate_temp_invoice_number()
    invoice.name = final_invoice_number
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    attach_pdf_to_invoice(pdf_attachment, invoice.name, "Wolt Invoice")
    notify_invoice_created("Wolt Invoice", invoice.name, invoice.invoice_number, communication_doc.subject)
    
    return invoice


def check_pdf_has_uber_eats_header(pdf_attachment):
    """PDF içinde 'Bestell- und Zahlungsübersicht' başlığı var mı kontrol et (UberEats faturaları için)"""
    try:
        import PyPDF2
        
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_path = file_doc.get_full_path()
        
        # Sadece ilk sayfayı oku (başlık genellikle ilk sayfada)
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            if len(pdf_reader.pages) > 0:
                first_page_text = pdf_reader.pages[0].extract_text()
                normalized = (first_page_text or "").lower()
                
                # "bestell- und zahlungsübersicht" başlığı olmalı
                has_header = "bestell- und zahlungsübersicht" in normalized or "bestell- und zahlungsübersicht" in first_page_text
                
                result = has_header
                print(f"[INVOICE] PDF UberEats header kontrolü: {pdf_attachment.file_name} → {result}")
                logger.debug(f"PDF UberEats header kontrolü: {pdf_attachment.file_name} → {result}")
                return result
        
        return False
    except Exception as e:
        print(f"[INVOICE] ⚠️ PDF UberEats header kontrolü hatası: {str(e)}")
        logger.warning(f"PDF UberEats header kontrolü hatası: {str(e)}")
        return False


def check_pdf_has_selbstfakturierung(pdf_attachment):
    """PDF içinde 'Rechnung(Selbstfakturierung)' başlığı var mı kontrol et (Wolt faturaları için)"""
    try:
        import PyPDF2
        
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_path = file_doc.get_full_path()
        
        # Sadece ilk sayfayı oku (başlık genellikle ilk sayfada)
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            if len(pdf_reader.pages) > 0:
                first_page_text = pdf_reader.pages[0].extract_text()
                normalized = (first_page_text or "").lower()
                
                # Hem "rechnung" hem de "selbstfakturierung" kelimeleri olmalı
                has_rechnung = "rechnung" in normalized
                has_selbstfakturierung = "selbstfakturierung" in normalized
                
                result = has_rechnung and has_selbstfakturierung
                print(f"[INVOICE] PDF Selbstfakturierung kontrolü: {pdf_attachment.file_name} → {result} (Rechnung: {has_rechnung}, Selbstfakturierung: {has_selbstfakturierung})")
                logger.debug(f"PDF Selbstfakturierung kontrolü: {pdf_attachment.file_name} → {result}")
                return result
        
        return False
    except Exception as e:
        print(f"[INVOICE] ⚠️ PDF Selbstfakturierung kontrolü hatası: {str(e)}")
        logger.warning(f"PDF Selbstfakturierung kontrolü hatası: {str(e)}")
        return False


def extract_invoice_data_from_pdf(pdf_attachment):
    """PDF'den fatura verilerini çıkar"""
    try:
        import PyPDF2
        
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_path = file_doc.get_full_path()
        
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            full_text = "".join(page.extract_text() for page in pdf_reader.pages)
        
        data = {
            "raw_text": full_text,
            "confidence": 60
        }
        
        # Rechnungsnummer extraction - UberEats faturaları için özel pattern (öncelikli)
        # Format: "Rechnungsnummer: UBER_DEU-FIGGGCEE-01-2025-0000001"
        uber_rechnung_match = re.search(r'Rechnungsnummer:\s*([A-Z0-9_\-]+)', full_text, re.IGNORECASE)
        if uber_rechnung_match:
            data["invoice_number"] = uber_rechnung_match.group(1).strip()
            print(f"[INVOICE] ✅ UberEats Rechnungsnummer bulundu: {data['invoice_number']}")
            logger.info(f"UberEats Rechnungsnummer bulundu: {data['invoice_number']}")
        else:
            # Rechnungsnummer extraction - Wolt faturaları için özel pattern
            # Format: "Rechnungsnummer DEU/25/HRB274170B/1/35" veya "Rechnungsnummer: DEU/25/HRB274170B/1/35"
            rechnung_match = re.search(r'Rechnungsnummer[\s:]+([A-Z]{3}/\d{2}/[A-Z0-9]+(?:/\d+)+)', full_text, re.IGNORECASE)
            if rechnung_match:
                data["invoice_number"] = rechnung_match.group(1).strip()
                print(f"[INVOICE] ✅ Rechnungsnummer bulundu: {data['invoice_number']}")
                logger.info(f"Rechnungsnummer bulundu: {data['invoice_number']}")
            else:
                # Fallback: Daha genel pattern'ler
                invoice_patterns = [
                    r'Rechnungsnummer[\s:]+([A-Z0-9\/\-]+)',
                    r'Invoice\s*(?:Number|No|#)[\s:]+([A-Z0-9\-]+)',
                    r'Rechnung\s*(?:Nr|#)[\s:]+([A-Z0-9\-]+)',
                    r'Fatura\s*(?:No|#)[\s:]+([A-Z0-9\-]+)',
                ]
                
                for pattern in invoice_patterns:
                    match = re.search(pattern, full_text, re.IGNORECASE)
                    if match:
                        invoice_num = match.group(1).strip()
                        # USt.-ID formatını (DE123456789) filtrele
                        if not re.match(r'^DE\d{9}$', invoice_num):
                            data["invoice_number"] = invoice_num
                            print(f"[INVOICE] ✅ Rechnungsnummer bulundu (fallback): {data['invoice_number']}")
                            logger.info(f"Rechnungsnummer bulundu (fallback): {data['invoice_number']}")
                            break
        
        date_patterns = [
            r'Date[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'Datum[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, full_text)
            if match:
                try:
                    data["invoice_date"] = parse_date(match.group(1))
                    break
                except:
                    pass
        
        total_patterns = [
            r'Total[\s:]*[€$£]?\s*([\d,\.]+)',
            r'Gesamt[\s:]*[€$£]?\s*([\d,\.]+)',
            r'Toplam[\s:]*[€$£]?\s*([\d,\.]+)',
            r'[€$£]\s*([\d,\.]+)',
        ]
        
        for pattern in total_patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            if matches:
                amounts = []
                for m in matches:
                    try:
                        amounts.append(float(m.replace(',', '')))
                    except:
                        pass
                if amounts:
                    data["total_amount"] = max(amounts)
                    break
        
        iban_match = re.search(r'([A-Z]{2}\d{2}[\s]?[\d\s]{10,30})', full_text)
        if iban_match:
            data["iban"] = iban_match.group(1).replace(' ', '')
        
        platform = detect_invoice_platform(full_text)
        data["platform"] = platform or "lieferando"
        
        if platform == "wolt":
            data.update(extract_wolt_fields(full_text))
        elif platform == "uber_eats":
            data.update(extract_uber_eats_fields(full_text))
        else:
            data.update(extract_lieferando_fields(full_text))
        
        return data
        
    except ImportError:
        return {"raw_text": "", "confidence": 0}
    except Exception as e:
        frappe.log_error(
            title="PDF Extraction Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return {"raw_text": "", "confidence": 0}


def detect_platform_from_filename(file_name: str) -> str:
    """Dosya adından platform tespit et"""
    if not file_name:
        print(f"[INVOICE] detect_platform_from_filename: Dosya adı boş")
        logger.debug("detect_platform_from_filename: Dosya adı boş")
        return None
    
    file_name_lower = file_name.lower()
    print(f"[INVOICE] detect_platform_from_filename: {file_name_lower}")
    logger.debug(f"detect_platform_from_filename: {file_name_lower}")
    
    # ÖNEMLİ: "rechnung_und" ile başlayan dosyalar kesinlikle Lieferando
    if file_name_lower.startswith("rechnung_und"):
        print(f"[INVOICE] ✅ Lieferando pattern eşleşti: rechnung_und (başlangıç)")
        logger.info("Lieferando pattern eşleşti: rechnung_und (başlangıç)")
        return "lieferando"
    
    # Wolt dosya adı pattern'leri:
    # - Edelweiss_Baumschulenstraße_2025-11-30_00:00:00.000_692cfcbbc3686f9e6b931ea6.pdf
    # - Edelweiss Baumschulenstraße__netting_report__semi_monthly__2025-11-16__2025-12-01.pdf
    # - Edelweiss Baumschulenstraße__sales_report__semi_monthly__2025-11-16__2025-12-01.pdf
    
    # Wolt pattern'leri: underscore ile ayrılmış tarih ve hash içeren dosyalar
    # veya __netting_report__ veya __sales_report__ içeren dosyalar
    wolt_patterns = [
        (r'__netting_report__', 'netting_report'),
        (r'__sales_report__', 'sales_report'),
        (r'_\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2}\.\d{3}_[a-f0-9]+\.pdf$', 'tarih_hash'),  # Tarih ve hash pattern
        (r'_\d{4}-\d{2}-\d{2}__\d{4}-\d{2}-\d{2}\.pdf$', 'tarih_araligi'),  # Tarih aralığı pattern
    ]
    
    for pattern, pattern_name in wolt_patterns:
        if re.search(pattern, file_name_lower):
            print(f"[INVOICE] ✅ Wolt pattern eşleşti: {pattern_name}")
            logger.info(f"Wolt pattern eşleşti: {pattern_name}")
            return "wolt"
    
    # Lieferando dosya adı pattern'leri (Wolt değilse)
    lieferando_patterns = [
        (r'^rechnung_und', 'rechnung_und_start'),  # Başlangıç kontrolü (zaten yukarıda kontrol edildi ama yine de)
        (r'lieferando', 'lieferando'),
        (r'yourdelivery', 'yourdelivery'),
        (r'takeaway', 'takeaway'),
        (r'rechnung_und', 'rechnung_und'),  # Herhangi bir yerde
    ]
    
    for pattern, pattern_name in lieferando_patterns:
        if re.search(pattern, file_name_lower):
            print(f"[INVOICE] ✅ Lieferando pattern eşleşti: {pattern_name}")
            logger.info(f"Lieferando pattern eşleşti: {pattern_name}")
            return "lieferando"
    
    print(f"[INVOICE] ⚠️ Dosya adından platform tespit edilemedi")
    logger.debug("Dosya adından platform tespit edilemedi")
    return None


def detect_invoice_platform(full_text: str) -> str:
    """PDF içeriğinden platform tespit et"""
    normalized = (full_text or "").lower()
    
    # ÖNEMLİ: "Bestell- und Zahlungsübersicht" UberEats faturalarının karakteristik özelliği
    if "bestell- und zahlungsübersicht" in normalized or "bestell- und zahlungsübersicht" in full_text:
        print(f"[INVOICE] ✅ UberEats tespit edildi: 'Bestell- und Zahlungsübersicht' başlığı bulundu")
        logger.info("UberEats tespit edildi: 'Bestell- und Zahlungsübersicht' başlığı bulundu")
        return "uber_eats"
    
    # UberEats kontrolü
    if "uber eats" in normalized or "uber eats germany" in normalized:
        return "uber_eats"
    
    # ÖNEMLİ: "Rechnung (Selbstfakturierung)" Wolt faturalarının karakteristik özelliği
    # Hem "rechnung" hem de "selbstfakturierung" olmalı
    if "rechnung" in normalized and "selbstfakturierung" in normalized:
        # Lieferando değilse Wolt olarak işaretle
        if "lieferando" not in normalized and "yourdelivery" not in normalized and "takeaway" not in normalized:
            print(f"[INVOICE] ✅ Wolt tespit edildi: 'Rechnung (Selbstfakturierung)' başlığı bulundu")
            logger.info("Wolt tespit edildi: 'Rechnung (Selbstfakturierung)' başlığı bulundu")
            return "wolt"
    
    # Wolt kontrolü
    if "wolt" in normalized and "lieferando" not in normalized:
        return "wolt"
    
    # Lieferando kontrolü
    if "lieferando" in normalized or "yourdelivery" in normalized or "takeaway" in normalized:
        return "lieferando"
    
    return "unknown"


def extract_lieferando_fields(full_text: str) -> dict:
    """Lieferando fatura alanlarını çıkar"""
    data = {}
    
    customer_num_match = re.search(r'Kundennummer[\s:]*(\d+)', full_text)
    if customer_num_match:
        data["customer_number"] = customer_num_match.group(1)
    
    restaurant_match = re.search(r'z\.Hd\.\s*(.+?)(?:\n|$)', full_text)
    if restaurant_match:
        data["restaurant_name"] = restaurant_match.group(1).strip()
    
    period_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+bis\s+(?:einschließlich\s+)?(\d{2}-\d{2}-\d{4})', full_text)
    if period_match:
        data["period_start"] = parse_date(period_match.group(1))
        data["period_end"] = parse_date(period_match.group(2))
    
    orders_match = re.search(r'(\d+)\s+Bestellung', full_text)
    if orders_match:
        data["total_orders"] = int(orders_match.group(1))
        data["online_paid_orders"] = int(orders_match.group(1))
    
    revenue_match = re.search(r'Ihr Umsatz in der Zeit[^€]*€\s*([\d,\.]+)', full_text)
    if revenue_match:
        amount = parse_decimal(revenue_match.group(1))
        if amount is not None:
            data["total_revenue"] = amount
            data["online_paid_amount"] = amount
    else:
        gesamt_match = re.search(r'Gesamt\s+\d+\s+Bestellung[^€]*€\s*([\d,\.]+)', full_text)
        if gesamt_match:
            amount = parse_decimal(gesamt_match.group(1))
            if amount is not None:
                data["total_revenue"] = amount
                data["online_paid_amount"] = amount
    
    service_fee_match = re.search(r'Servicegebühr:\s*([\d,\.]+)%[^€]*€\s*[\d,\.]+\s*€\s*([\d,\.]+)', full_text)
    if service_fee_match:
        try:
            data["service_fee_rate"] = float(service_fee_match.group(1).replace(',', '.'))
        except ValueError:
            pass
        amount = parse_decimal(service_fee_match.group(2))
        if amount is not None:
            data["service_fee_amount"] = amount
    
    admin_fee_match = re.search(r'Verwaltungsgebühr.*?\n\s*Servicegebühr:\s*€\s*([\d,\.]+)\s+x\s+\d+', full_text, re.DOTALL)
    if admin_fee_match:
        amount = parse_decimal(admin_fee_match.group(1))
        if amount is not None:
            data["admin_fee_amount"] = amount
    
    subtotal_match = re.search(r'Zwischensumme\s*€\s*([\d,\.]+)', full_text)
    if subtotal_match:
        amount = parse_decimal(subtotal_match.group(1))
        if amount is not None:
            data["subtotal"] = amount
    
    tax_match = re.search(r'MwSt\.\s*\((\d+)%[^€]*€\s*[\d,\.]+\)\s*€\s*([\d,\.]+)', full_text)
    if tax_match:
        try:
            data["tax_rate"] = float(tax_match.group(1))
        except ValueError:
            pass
        amount = parse_decimal(tax_match.group(2))
        if amount is not None:
            data["tax_amount"] = amount
    
    total_match = re.search(r'Gesamtbetrag dieser Rechnung\s*€\s*([\d,\.]+)', full_text)
    if total_match:
        amount = parse_decimal(total_match.group(1))
        if amount is not None:
            data["total_amount"] = amount
    
    paid_match = re.search(r'Verrechnet mit eingegangenen Onlinebezahlungen\s*€\s*([\d,\.]+)', full_text)
    if paid_match:
        amount = parse_decimal(paid_match.group(1))
        if amount is not None:
            data["paid_online_payments"] = amount
    
    outstanding_match = re.search(r'Offener Rechnungsbetrag\s*€\s*([\d,\.]+)', full_text)
    if outstanding_match:
        amount = parse_decimal(outstanding_match.group(1))
        if amount is not None:
            data["outstanding_amount"] = amount
    
    ausstehende_match = re.search(r'Ausstehende Onlinebezahlungen am[^€]*€\s*([\d,\.]+)', full_text)
    if ausstehende_match:
        amount = parse_decimal(ausstehende_match.group(1))
        if amount is not None:
            data["outstanding_balance"] = amount
    
    auszahlung_gesamt_match = re.search(r'COLLECTIVE GmbH[^€]*€\s*([\d,\.]+)\s*Datum', full_text, re.DOTALL)
    if auszahlung_gesamt_match:
        amount = parse_decimal(auszahlung_gesamt_match.group(1))
        if amount is not None:
            data["payout_amount"] = amount
    
    company_match = re.search(r'z\.Hd\.\s+(.+?GmbH)', full_text)
    if company_match:
        data["customer_company"] = company_match.group(1).strip()
    
    cust_iban_match = re.search(r'Bankkonto\s+(DE[\d\s]+)', full_text)
    if cust_iban_match:
        data["customer_bank_iban"] = cust_iban_match.group(1).replace(' ', '')
    
    supp_iban_match = re.search(r'IBAN:\s+(DE[\d\s]+)', full_text)
    if supp_iban_match:
        data["supplier_iban"] = supp_iban_match.group(1).replace(' ', '')
    
    ust_match = re.search(r'USt\.-IdNr\.\s+(DE\d+)', full_text)
    if ust_match:
        data["supplier_ust_idnr"] = ust_match.group(1)
    
    return data


def extract_wolt_fields(full_text: str) -> dict:
    """Wolt fatura alanlarını çıkar"""
    data = {"platform": "wolt"}
    clean_text = (full_text or "").replace("|", " ")
    
    # Rechnungsnummer extraction - Wolt faturaları için özel format
    # Format: "Rechnungsnummer DEU/25/HRB274170B/1/35" veya "Rechnungsnummer: DEU/25/HRB274170B/1/35"
    rechnung_match = re.search(r'Rechnungsnummer[\s:]+([A-Z]{3}/\d{2}/[A-Z0-9]+(?:/\d+)+)', full_text, re.IGNORECASE)
    if rechnung_match:
        data["invoice_number"] = rechnung_match.group(1).strip()
        print(f"[INVOICE] ✅ Wolt Rechnungsnummer bulundu: {data['invoice_number']}")
        logger.info(f"Wolt Rechnungsnummer bulundu: {data['invoice_number']}")
    
    supplier_match = re.search(r'Bill To\s+(.*?)Leistungszeitraum', full_text, re.DOTALL)
    if supplier_match:
        block = supplier_match.group(1)
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines:
            data["supplier_name"] = lines[0]
        address_lines = lines[1:]
        if address_lines:
            data["supplier_address"] = " ".join(address_lines)
    else:
        data["supplier_name"] = "Wolt Enterprises Deutschland GmbH"
    
    supplier_vat_match = re.search(r'USt\.-ID:\s*(DE\d+)', full_text)
    if supplier_vat_match:
        data["supplier_vat"] = supplier_vat_match.group(1)
    
    invoice_date_match = re.search(r'Rechnungsdatum\s+(\d{2}\.\d{2}\.\d{4})', full_text)
    if invoice_date_match:
        data["invoice_date"] = parse_date(invoice_date_match.group(1))
    
    period_match = re.search(r'Leistungszeitraum\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})', full_text)
    if period_match:
        data["period_start"] = parse_date(period_match.group(1))
        data["period_end"] = parse_date(period_match.group(2))
    
    restaurant_match = re.search(r'Restaurant\s+([^\n]+)', full_text)
    if restaurant_match:
        data["restaurant_name"] = restaurant_match.group(1).strip()
    
    business_id_match = re.search(r'Geschäfts-ID:\s*([A-Z0-9 ]+)', full_text)
    if business_id_match:
        data["customer_number"] = business_id_match.group(1).strip()
    
    goods_matches = re.findall(r'Summe verkaufte Waren\s+([\-\d,\.]+)\s+(7\.00|19\.00)\s+([\-\d,\.]+)\s+([\-\d,\.]+)', clean_text)
    for net, rate, vat, gross in goods_matches:
        parsed = (
            parse_decimal(net),
            parse_decimal(vat),
            parse_decimal(gross),
        )
        if rate.startswith("7"):
            data["goods_net_7"], data["goods_vat_7"], data["goods_gross_7"] = parsed
        else:
            data["goods_net_19"], data["goods_vat_19"], data["goods_gross_19"] = parsed
    
    goods_total_match = re.search(r'Zwischensumme aller verkauften Waren \(A\)\s+([\-\d,\.]+)\s+([\-\d,\.]+)\s+([\-\d,\.]+)', clean_text)
    if goods_total_match:
        data["goods_net_total"] = parse_decimal(goods_total_match.group(1))
        data["goods_vat_total"] = parse_decimal(goods_total_match.group(2))
        data["goods_gross_total"] = parse_decimal(goods_total_match.group(3))
    
    distribution_match = re.search(r'Zwischensumme Wolt Vertrieb \(B\)\s+([\-\d,\.]+)\s+([\-\d,\.]+)\s+([\-\d,\.]+)', clean_text)
    if distribution_match:
        data["distribution_net_total"] = parse_decimal(distribution_match.group(1))
        data["distribution_vat_total"] = parse_decimal(distribution_match.group(2))
        data["distribution_gross_total"] = parse_decimal(distribution_match.group(3))
    
    netprice_matches = re.findall(
        r'Summe Nettopreis \(A\s*-\s*B\) mit Umsatzsteuer\s+(7\.00|19\.00)\s*%[\s|]+([\-\d,\.]+)[\s|]+(?:7\.00|19\.00)[\s|]+([\-\d,\.]+)[\s|]+([\-\d,\.]+)',
        clean_text
    )
    for rate, net, vat, gross in netprice_matches:
        values = (
            parse_decimal(net),
            parse_decimal(vat),
            parse_decimal(gross),
        )
        if rate.startswith("7"):
            data["netprice_net_7"], data["netprice_vat_7"], data["netprice_gross_7"] = values
        else:
            data["netprice_net_19"], data["netprice_vat_19"], data["netprice_gross_19"] = values
    
    if any(key in data for key in ("netprice_net_7", "netprice_net_19")):
        data["netprice_net_total"] = (data.get("netprice_net_7") or 0) + (data.get("netprice_net_19") or 0)
        data["netprice_vat_total"] = (data.get("netprice_vat_7") or 0) + (data.get("netprice_vat_19") or 0)
        data["netprice_gross_total"] = (data.get("netprice_gross_7") or 0) + (data.get("netprice_gross_19") or 0)
    
    end_amount_match = re.search(r'Endbetrag\s+([\-\d,\.]+)\s+([\-\d,\.]+)\s+([\-\d,\.]+)', clean_text)
    if end_amount_match:
        data["end_amount_net"] = parse_decimal(end_amount_match.group(1))
        data["end_amount_vat"] = parse_decimal(end_amount_match.group(2))
        data["end_amount_gross"] = parse_decimal(end_amount_match.group(3))
        data["total_amount"] = data.get("end_amount_gross")
    
    return data


def extract_uber_eats_fields(full_text: str) -> dict:
    """UberEats fatura alanlarını çıkar"""
    data = {"platform": "uber_eats"}
    clean_text = (full_text or "").replace("|", " ")
    
    # Rechnungsnummer extraction - UberEats formatı: UBER_DEU-FIGGGCEE-01-2025-0000001
    rechnung_match = re.search(r'Rechnungsnummer:\s*([A-Z0-9_\-]+)', full_text, re.IGNORECASE)
    if rechnung_match:
        data["invoice_number"] = rechnung_match.group(1).strip()
        print(f"[INVOICE] ✅ UberEats Rechnungsnummer bulundu: {data['invoice_number']}")
        logger.info(f"UberEats Rechnungsnummer bulundu: {data['invoice_number']}")
    
    # Rechnungsdatum
    invoice_date_match = re.search(r'Rechnungsdatum:\s*(\d{2}\.\d{2}\.\d{4})', full_text)
    if invoice_date_match:
        data["invoice_date"] = parse_date(invoice_date_match.group(1))
    
    # Steuerdatum
    tax_date_match = re.search(r'Steuerdatum\s+(\d{2}\.\d{2}\.\d{4})', full_text)
    if tax_date_match:
        data["tax_date"] = parse_date(tax_date_match.group(1))
    
    # Zeitraum
    period_match = re.search(r'Zeitraum:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})', full_text)
    if period_match:
        data["period_start"] = parse_date(period_match.group(1))
        data["period_end"] = parse_date(period_match.group(2))
    else:
        # Alternatif format: "vom 11.11.2025 bis zum 16.11.2025"
        period_match2 = re.search(r'vom\s+(\d{2}\.\d{2}\.\d{4})\s+bis\s+(?:zum\s+)?(\d{2}\.\d{2}\.\d{4})', full_text)
        if period_match2:
            data["period_start"] = parse_date(period_match2.group(1))
            data["period_end"] = parse_date(period_match2.group(2))
    
    # Customer company (CC CULINARY COLLECTIVE GmbH)
    customer_company_match = re.search(r'CC CULINARY COLLECTIVE GmbH', full_text, re.IGNORECASE)
    if customer_company_match:
        data["customer_company"] = "CC CULINARY COLLECTIVE GmbH"
    
    # Restaurant name
    # Önce "Restaurant:" etiketi ile arama
    restaurant_match = re.search(r'Restaurant:\s*([^\n]+)', full_text)
    if restaurant_match:
        data["restaurant_name"] = restaurant_match.group(1).strip()
    else:
        # Alternatif: "Burger Boost - CC Culinary Collective (Weseler Straße)" formatı
        # "Rechnung" bölümünde veya "Umsatzbericht" bölümünde olabilir
        restaurant_match2 = re.search(r'Burger Boost\s*-\s*CC Culinary Collective\s*\(([^\)]+)\)', full_text, re.IGNORECASE | re.DOTALL)
        if restaurant_match2:
            location = restaurant_match2.group(1).strip()
            data["restaurant_name"] = f"Burger Boost - CC Culinary Collective ({location})"
        else:
            # Daha genel pattern: "Burger Boost - CC Culinary Collective" (parantez olmadan)
            restaurant_match3 = re.search(r'(Burger Boost\s*-\s*CC Culinary Collective[^\n]*)', full_text, re.IGNORECASE)
            if restaurant_match3:
                data["restaurant_name"] = restaurant_match3.group(1).strip()
    
    # Restaurant address (Hohenzollerndamm 58,14199,Berlin, Germany)
    # "Rechnung" bölümünden sonraki adres bilgisi
    # Format: "Hohenzollerndamm 58,14199,Berlin\nGermany" veya "Hohenzollerndamm 58,14199,Berlin, Germany"
    address_match = re.search(r'Hohenzollerndamm\s+(\d+)[,\s]+(\d+)[,\s]+([A-Za-z]+)[,\s]*([A-Za-z]+)?', full_text, re.IGNORECASE | re.MULTILINE)
    if address_match:
        street = address_match.group(1)
        postal = address_match.group(2)
        city = address_match.group(3)
        country = address_match.group(4) or "Germany"
        data["restaurant_address"] = f"Hohenzollerndamm {street}, {postal}, {city}, {country}"
    else:
        # Alternatif: "CC CULINARY COLLECTIVE GmbH" sonrasındaki adres satırları
        address_match2 = re.search(r'CC CULINARY COLLECTIVE GmbH\s+([^\n]+)\s+([^\n]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if address_match2:
            line1 = address_match2.group(1).strip()
            line2 = address_match2.group(2).strip()
            data["restaurant_address"] = f"{line1}, {line2}"
    
    # Handelsregisternummer (HRB 274170)
    hrb_match = re.search(r'Handelsregisternummer:\s*([A-Z0-9\s]+)', full_text, re.IGNORECASE)
    if hrb_match:
        data["business_id"] = hrb_match.group(1).strip()
    
    # USt-IdNr. (DE361596531) - Müşteri USt-ID
    customer_vat_match = re.search(r'USt-IdNr\.:\s*(DE\d+)', full_text, re.IGNORECASE)
    if customer_vat_match:
        data["customer_vat"] = customer_vat_match.group(1).strip()
    
    # St-Nr. (127/249/52915) - Vergi numarası
    tax_number_match = re.search(r'St-Nr\.:\s*([\d\/]+)', full_text, re.IGNORECASE)
    if tax_number_match:
        data["tax_number"] = tax_number_match.group(1).strip()
    
    # Total orders
    orders_match = re.search(r'(\d+)\s+Bestellungen im Gesamtwert', full_text)
    if orders_match:
        data["total_orders"] = int(orders_match.group(1))
    
    # Total order value
    order_value_match = re.search(r'Bestellungen im Gesamtwert von:\s*€\s*([\d,\.]+)', full_text)
    if order_value_match:
        data["total_order_value"] = parse_decimal(order_value_match.group(1))
    
    # Gross revenue after discounts
    gross_revenue_match = re.search(r'Bruttoumsatz nach Rabatten\s*€\s*([\d,\.]+)', full_text)
    if gross_revenue_match:
        data["gross_revenue_after_discounts"] = parse_decimal(gross_revenue_match.group(1))
    
    # Commission own delivery
    commission_own_match = re.search(r'Provision, eigene Lieferung.*?€\s*([\d,\.]+)', full_text)
    if commission_own_match:
        data["commission_own_delivery"] = parse_decimal(commission_own_match.group(1))
    
    # Commission pickup
    commission_pickup_match = re.search(r'Provision, Abholung.*?€\s*([\d,\.]+)', full_text)
    if commission_pickup_match:
        data["commission_pickup"] = parse_decimal(commission_pickup_match.group(1))
    
    # Uber Eats fee
    uber_fee_match = re.search(r'Uber Eats Gebühr\s*€\s*([\d,\.]+)', full_text)
    if uber_fee_match:
        data["uber_eats_fee"] = parse_decimal(uber_fee_match.group(1))
    
    # VAT 19%
    vat_match = re.search(r'MwSt\.\s*\(19%[^€]*€\s*([\d,\.]+)', full_text)
    if vat_match:
        data["vat_19_percent"] = parse_decimal(vat_match.group(1))
    
    # Cash collected
    cash_match = re.search(r'Eingenommenes Bargeld\s*€\s*([\d,\.]+)', full_text)
    if cash_match:
        data["cash_collected"] = parse_decimal(cash_match.group(1))
    
    # Total payout
    payout_match = re.search(r'Gesamtauszahlung\s*€\s*([\d,\.]+)', full_text)
    if payout_match:
        data["total_payout"] = parse_decimal(payout_match.group(1))
    
    # Net amount
    net_match = re.search(r'Gesamtnettobetrag\s*([\d,\.]+)\s*€', full_text)
    if net_match:
        data["net_amount"] = parse_decimal(net_match.group(1))
    
    # VAT amount
    vat_amount_match = re.search(r'Gesamtbetrag USt 19%\s*([\d,\.]+)\s*€', full_text)
    if vat_amount_match:
        data["vat_amount"] = parse_decimal(vat_amount_match.group(1))
    
    # Total amount
    total_match = re.search(r'Gesamtbetrag\s*([\d,\.]+)\s*€', full_text)
    if total_match:
        data["total_amount"] = parse_decimal(total_match.group(1))
    
    return data


def create_uber_eats_invoice_doc(communication_doc, pdf_attachment, extracted_data):
    """UberEats Invoice kaydı oluştur"""
    invoice_number = extracted_data.get("invoice_number")
    
    # Duplicate kontrolü: Sadece invoice_number (Rechnungsnummer) ile kontrol
    if invoice_number:
        existing_invoice = frappe.db.exists("Uber Eats Invoice", {"invoice_number": invoice_number})
        if existing_invoice:
            print(f"[INVOICE] ⚠️ Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            logger.info(f"Fatura zaten işlenmiş (Rechnungsnummer: {invoice_number})")
            return None
        print(f"[INVOICE] ✅ Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
        logger.info(f"Yeni fatura tespit edildi (Rechnungsnummer: {invoice_number})")
    else:
        print(f"[INVOICE] ⚠️ Invoice number bulunamadı, geçici numara kullanılacak")
        logger.warning("Invoice number bulunamadı, geçici numara kullanılacak")
    
    invoice = frappe.get_doc({
        "doctype": "Uber Eats Invoice",
        "invoice_number": invoice_number or generate_temp_invoice_number(),
        "invoice_date": extracted_data.get("invoice_date") or frappe.utils.today(),
        "tax_date": extracted_data.get("tax_date"),
        "period_start": extracted_data.get("period_start"),
        "period_end": extracted_data.get("period_end"),
        "status": "Draft",
        "supplier_name": extracted_data.get("supplier_name") or "Uber Eats Germany GmbH",
        "supplier_vat": extracted_data.get("supplier_vat"),
        "supplier_address": extracted_data.get("supplier_address"),
        "restaurant_name": extracted_data.get("restaurant_name"),
        "customer_company": extracted_data.get("customer_company"),
        "restaurant_address": extracted_data.get("restaurant_address"),
        "business_id": extracted_data.get("business_id"),
        "customer_vat": extracted_data.get("customer_vat"),
        "tax_number": extracted_data.get("tax_number"),
        "total_orders": extracted_data.get("total_orders") or 0,
        "total_order_value": extracted_data.get("total_order_value") or 0,
        "gross_revenue_after_discounts": extracted_data.get("gross_revenue_after_discounts") or 0,
        "commission_own_delivery": extracted_data.get("commission_own_delivery") or 0,
        "commission_pickup": extracted_data.get("commission_pickup") or 0,
        "uber_eats_fee": extracted_data.get("uber_eats_fee") or 0,
        "vat_19_percent": extracted_data.get("vat_19_percent") or 0,
        "cash_collected": extracted_data.get("cash_collected") or 0,
        "total_payout": extracted_data.get("total_payout") or 0,
        "net_amount": extracted_data.get("net_amount") or 0,
        "vat_amount": extracted_data.get("vat_amount") or 0,
        "total_amount": extracted_data.get("total_amount") or 0,
        "email_subject": communication_doc.subject,
        "email_from": communication_doc.sender,
        "received_date": communication_doc.creation,
        "processed_date": frappe.utils.now(),
        "extraction_confidence": extracted_data.get("confidence", 55),
        "raw_text": extracted_data.get("raw_text", "")
    })
    
    # name (ID) field'ını invoice_number (Rechnungsnummer) ile aynı yap
    final_invoice_number = invoice_number or generate_temp_invoice_number()
    invoice.name = final_invoice_number
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    attach_pdf_to_invoice(pdf_attachment, invoice.name, "Uber Eats Invoice")
    notify_invoice_created("Uber Eats Invoice", invoice.name, invoice.invoice_number, communication_doc.subject)
    
    return invoice


def parse_decimal(value: str | None):
    """String değeri decimal'e çevir"""
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    clean = clean.replace("€", "").replace("%", "").replace("−", "-").replace(" ", "")
    
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    else:
        clean = clean.replace(",", ".")
    
    try:
        return float(clean)
    except ValueError:
        return None


def attach_pdf_to_invoice(pdf_attachment, invoice_name, target_doctype):
    """PDF'i Invoice kaydına attach et"""
    try:
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_content = file_doc.get_content()
        
        new_file = frappe.get_doc({
            "doctype": "File",
            "file_name": file_doc.file_name,
            "attached_to_doctype": target_doctype,
            "attached_to_name": invoice_name,
            "attached_to_field": "pdf_file",
            "is_private": 0,
            "content": file_content,
            "folder": "Home/Attachments"
        })
        new_file.flags.ignore_permissions = True
        new_file.insert()
        
        frappe.db.set_value(target_doctype, invoice_name, "pdf_file", new_file.file_url)
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(
            title="PDF Attachment Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def generate_temp_invoice_number():
    """Geçici fatura numarası oluştur"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"TEMP-{timestamp}"


def parse_date(date_str):
    """Çeşitli tarih formatlarını parse et"""
    formats = [
        "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
        "%m/%d/%Y", "%d.%m.%y", "%d/%m/%y",
    ]
    
    for fmt in formats:
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            return parsed_date.strftime("%Y-%m-%d")
        except:
            continue
    
    return frappe.utils.today()


def notify_invoice_created(doctype, docname, invoice_number, email_subject):
    """Fatura oluşturulduğunda kullanıcıya bildirim göster"""
    try:
        from frappe.utils.data import get_url_to_form
        
        invoice_link = get_url_to_form(doctype, docname)
        platform_name = "Lieferando" if "Lieferando" in doctype else "Wolt"
        
        message = f"""
        <b>{platform_name} Faturası Oluşturuldu</b><br><br>
        Fatura No: <b>{invoice_number or 'N/A'}</b><br>
        Email: {email_subject[:50]}{'...' if len(email_subject) > 50 else ''}<br><br>
        <a href='{invoice_link}'><b>Faturayı Görüntüle</b></a>
        """
        
        frappe.publish_realtime(
            "msgprint",
            {
                "message": message,
                "alert": True,
                "indicator": "green",
                "title": f"{platform_name} Faturası Oluşturuldu"
            },
            after_commit=True
        )
        
    except Exception as e:
        logger.error(f"Bildirim gönderme hatası: {str(e)}")


def _get_session_stats():
    """Session bazlı istatistikleri al"""
    session_key = "invoice_processing_stats"
    if not hasattr(frappe.local, session_key):
        setattr(frappe.local, session_key, {
            "total_detected": 0,
            "already_processed": 0,
            "newly_processed": 0,
            "errors": 0,
            "invoices_created": [],
            "emails_processed": []
        })
    return getattr(frappe.local, session_key)


def _update_session_stats(stats):
    """Session istatistiklerini güncelle"""
    session_stats = _get_session_stats()
    session_stats["total_detected"] += stats.get("total_detected", 0)
    session_stats["already_processed"] += stats.get("already_processed", 0)
    session_stats["newly_processed"] += stats.get("newly_processed", 0)
    session_stats["errors"] += stats.get("errors", 0)
    session_stats["invoices_created"].extend(stats.get("invoices_created", []))
    if stats.get("total_detected", 0) > 0 or stats.get("already_processed", 0) > 0:
        session_stats["emails_processed"].append(stats)


def show_summary_notification(stats, email_subject, is_final=False):
    """Email işleme özetini göster - hem realtime hem de Notification Log olarak"""
    print(f"[INVOICE] show_summary_notification çağrıldı. Stats: {stats}, Subject: {email_subject}")
    try:
        from frappe.utils.data import get_url_to_form
        from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
        
        print(f"[INVOICE] Import'lar tamamlandı")
        
        try:
            _update_session_stats(stats)
            print(f"[INVOICE] Session stats güncellendi")
        except Exception as e:
            print(f"[INVOICE] ⚠️ Session stats hatası (devam ediliyor): {str(e)}")
            logger.warning(f"Session stats hatası: {str(e)}")
        
        total_detected = stats.get("total_detected", 0)
        already_processed = stats.get("already_processed", 0)
        newly_processed = stats.get("newly_processed", 0)
        errors = stats.get("errors", 0)
        invoices_created = stats.get("invoices_created", [])
        
        print(f"[INVOICE] Bildirim gönderiliyor. Stats: total={total_detected}, new={newly_processed}, already={already_processed}, errors={errors}")
        logger.info(f"Bildirim gönderiliyor. Stats: total={total_detected}, new={newly_processed}, already={already_processed}, errors={errors}")
        
        if total_detected == 0 and already_processed == 0:
            print(f"[INVOICE] Bildirim gönderilmedi - istatistik yok (total={total_detected}, already={already_processed})")
            logger.info("Bildirim gönderilmedi - istatistik yok")
            return
        
        message_parts = []
        message_parts.append(f"📧 <b>Email İşleme Özeti</b><br>")
        message_parts.append(f"<b>Email:</b> {email_subject[:60]}{'...' if len(email_subject) > 60 else ''}<br><br>")
        
        if total_detected > 0:
            message_parts.append(f"✅ <b>Yakalanan Fatura:</b> {total_detected}<br>")
        
        if already_processed > 0:
            message_parts.append(f"⚠️ <b>Daha Önce İşlenmiş:</b> {already_processed}<br>")
        
        if newly_processed > 0:
            message_parts.append(f"🆕 <b>Yeni İşlenen:</b> {newly_processed}<br>")
        
        if errors > 0:
            message_parts.append(f"❌ <b>Hata:</b> {errors}<br>")
        
        if invoices_created:
            message_parts.append(f"<br><b>Oluşturulan Faturalar:</b><br>")
            for inv in invoices_created[:5]:
                platform = "Lieferando" if "Lieferando" in inv["doctype"] else "Wolt"
                invoice_link = get_url_to_form(inv["doctype"], inv["name"])
                message_parts.append(f"• <a href='{invoice_link}'>{platform} - {inv['invoice_number']}</a><br>")
            
            if len(invoices_created) > 5:
                message_parts.append(f"... ve {len(invoices_created) - 5} fatura daha<br>")
        
        message = "".join(message_parts)
        
        if errors > 0:
            indicator = "red"
        elif already_processed > 0 and newly_processed == 0:
            indicator = "orange"
        else:
            indicator = "green"
        
        # Realtime bildirim (anlık popup) - her zaman gönder
        print(f"[INVOICE] Realtime bildirim hazırlanıyor...")
        try:
            current_user = frappe.session.user if hasattr(frappe, 'session') and hasattr(frappe.session, 'user') else None
            print(f"[INVOICE] Current user: {current_user}")
            
            # Tüm aktif kullanıcılara bildirim gönder
            active_users = frappe.get_all("User", 
                filters={"enabled": 1, "user_type": "System User"},
                fields=["name"]
            )
            user_list = [user.name for user in active_users] if active_users else []
            
            if not user_list:
                print(f"[INVOICE] ⚠️ Aktif kullanıcı bulunamadı, bildirim gönderilemiyor")
                logger.warning("Aktif kullanıcı bulunamadı")
            else:
                print(f"[INVOICE] Bildirim gönderilecek kullanıcılar: {user_list}")
                
                # Her kullanıcıya bildirim gönder
                for user in user_list:
                    try:
                        frappe.publish_realtime(
                            "show_alert",
                            {
                                "message": message,
                                "alert": True,
                                "indicator": indicator,
                                "title": "Fatura İşleme Özeti"
                            },
                            user=user,
                            after_commit=True
                        )
                        print(f"[INVOICE] ✅ Bildirim gönderildi: {user}")
                    except Exception as e:
                        print(f"[INVOICE] ❌ Kullanıcı {user} için bildirim hatası: {str(e)}")
                        logger.error(f"Kullanıcı {user} için bildirim hatası: {str(e)}")
                
                logger.info(f"Realtime bildirim gönderildi - {len(user_list)} kullanıcıya")
        except Exception as e:
            print(f"[INVOICE] ❌ Realtime bildirim hatası: {str(e)}")
            logger.error(f"Realtime bildirim hatası: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Notification Log kaydı oluştur (kalıcı bildirim)
        try:
            subject_text = f"Fatura İşleme: {newly_processed} yeni, {already_processed} tekrar"
            if errors > 0:
                subject_text += f", {errors} hata"
            
            notification_doc = {
                "type": "Alert",
                "document_type": "Communication",
                "subject": subject_text,
                "email_content": message,
            }
            
            active_users = frappe.get_all("User", 
                filters={"enabled": 1, "user_type": "System User"},
                fields=["name"]
            )
            user_emails = [user.name for user in active_users]
            
            if user_emails:
                enqueue_create_notification(user_emails, notification_doc)
                print(f"[INVOICE] ✅ Notification Log gönderildi - {len(user_emails)} kullanıcıya")
                logger.info(f"Notification Log gönderildi - {len(user_emails)} kullanıcıya")
            else:
                print(f"[INVOICE] ⚠️ Notification Log gönderilmedi - aktif kullanıcı bulunamadı")
                logger.warning("Notification Log gönderilmedi - aktif kullanıcı bulunamadı")
        except Exception as e:
            logger.error(f"Notification Log gönderme hatası: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Final özet için toplu bildirim gönder
        if is_final:
            session_stats = _get_session_stats()
            _send_final_summary(session_stats)
            if hasattr(frappe.local, "invoice_processing_stats"):
                delattr(frappe.local, "invoice_processing_stats")
        
    except Exception as e:
        logger.error(f"Özet bildirimi gönderme hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


def _send_final_summary(session_stats):
    """Tüm email'ler işlendikten sonra toplu özet gönder"""
    try:
        from frappe.utils.data import get_url_to_form
        from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
        
        total_detected = session_stats.get("total_detected", 0)
        already_processed = session_stats.get("already_processed", 0)
        newly_processed = session_stats.get("newly_processed", 0)
        errors = session_stats.get("errors", 0)
        all_invoices = session_stats.get("invoices_created", [])
        emails_count = len(session_stats.get("emails_processed", []))
        
        if total_detected == 0 and already_processed == 0:
            return
        
        message_parts = []
        message_parts.append(f"<b>📧 Toplu Email İşleme Özeti</b><br><br>")
        message_parts.append(f"<b>İşlenen Email Sayısı:</b> {emails_count}<br><br>")
        message_parts.append(f"✅ <b>Toplam Yakalanan Fatura:</b> {total_detected}<br>")
        message_parts.append(f"🆕 <b>Yeni İşlenen:</b> {newly_processed}<br>")
        message_parts.append(f"⚠️ <b>Daha Önce İşlenmiş:</b> {already_processed}<br>")
        
        if errors > 0:
            message_parts.append(f"❌ <b>Hata:</b> {errors}<br>")
        
        if all_invoices:
            message_parts.append(f"<br><b>Oluşturulan Faturalar ({len(all_invoices)}):</b><br>")
            for inv in all_invoices[:10]:
                platform = "Lieferando" if "Lieferando" in inv["doctype"] else "Wolt"
                invoice_link = get_url_to_form(inv["doctype"], inv["name"])
                message_parts.append(f"• <a href='{invoice_link}'>{platform} - {inv['invoice_number']}</a><br>")
            
            if len(all_invoices) > 10:
                message_parts.append(f"... ve {len(all_invoices) - 10} fatura daha<br>")
        
        message = "".join(message_parts)
        
        if errors > 0:
            indicator = "red"
        elif already_processed > 0 and newly_processed == 0:
            indicator = "orange"
        else:
            indicator = "green"
        
        # Toplu özet bildirimi
        frappe.publish_realtime(
            "msgprint",
            {
                "message": message,
                "alert": True,
                "indicator": indicator,
                "title": "Fatura İşleme - Toplu Özet"
            },
            after_commit=True
        )
        
        subject_text = f"Fatura İşleme Özeti: {emails_count} email, {newly_processed} yeni fatura"
        if errors > 0:
            subject_text += f", {errors} hata"
        
        notification_doc = {
            "type": "Alert",
            "document_type": "Communication",
            "subject": subject_text,
            "email_content": message,
        }
        
        active_users = frappe.get_all("User", 
            filters={"enabled": 1, "user_type": "System User"},
            fields=["name"]
        )
        user_emails = [user.name for user in active_users]
        
        if user_emails:
            enqueue_create_notification(user_emails, notification_doc)
        
    except Exception as e:
        logger.error(f"Toplu özet bildirimi gönderme hatası: {str(e)}")
