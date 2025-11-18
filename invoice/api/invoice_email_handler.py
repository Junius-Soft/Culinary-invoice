"""
Gmail'den gelen fatura email'lerini otomatik işleyen modül
"""

import frappe
import re
from datetime import datetime

def process_invoice_email(doc, method=None):
    """
    Communication DocType'ına gelen email'leri yakala
    Subject'te 'invoice' veya 'fatura' varsa PDF Invoice oluştur
    
    Hook: Communication -> after_insert, on_update
    """
    try:
        # Sadece gelen email'leri işle
        if doc.communication_type != "Communication":
            return
        
        if doc.sent_or_received != "Received":
            return
        
        # Bu email zaten işlendi mi kontrol et (duplicate önleme)
        existing_invoice = frappe.db.exists("Lieferando Invoice", {
            "email_from": doc.sender,
            "email_subject": doc.subject,
            "received_date": doc.creation
        })
        
        if existing_invoice:
            print(f">>>>>> Email zaten işlenmiş, atlandı: {doc.subject}")
            return
        
        # Subject kontrolü - invoice/fatura içeriyor mu?----------------------------------
        subject = (doc.subject or "").lower()
        keywords = ["invoice", "fatura", "rechnung", "facture", "bill"]
        
        if not any(keyword in subject for keyword in keywords):
            print(f">>>>>> Email '{doc.subject}' fatura değil, atlandı")
            return
        
        print(f">>>>>> FATURA EMAIL'İ ALGILANDI: {doc.subject}")
        
        # PDF attachments'ları bul
        attachments = frappe.get_all("File",
            filters={
                "attached_to_doctype": "Communication",
                "attached_to_name": doc.name,
            },
            fields=["name", "file_url", "file_name", "file_size"]
        )
        
        # DEBUG: Tüm attachments'ları göster
        print(f">>>>>> Toplam {len(attachments)} attachment bulundu")
        for att in attachments:
            print(f">>>>>> Attachment: name={att.get('name')}, file_name={att.get('file_name')}, file_url={att.get('file_url')}")
        
        # Sadece PDF'leri filtrele
        pdf_attachments = [
            att for att in attachments 
            if att.get('file_name') and att.get('file_name').lower().endswith('.pdf')
        ]
        
        if not pdf_attachments:
            print(f">>>>>> Email'de PDF bulunamadı: {doc.subject}")
            print(f">>>>>> Kontrol edilen {len(attachments)} attachment'tan hiçbiri PDF değil")
            return
        
        print(f">>>>>> {len(pdf_attachments)} adet PDF bulundu")
        
        # Her PDF için Invoice oluştur
        for pdf in pdf_attachments:
            try:
                create_invoice_from_pdf(doc, pdf)
            except Exception as e:
                frappe.log_error(
                    title="Invoice PDF Processing Error",
                    message=f"PDF: {pdf.file_name}\nError: {str(e)}\n{frappe.get_traceback()}"
                )
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(
            title="Invoice Email Processing Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def create_invoice_from_pdf(communication_doc, pdf_attachment):
    """
    PDF'den Invoice kaydı oluştur
    """
    print(f">>>>>> İşleniyor: {pdf_attachment.file_name}")
    
    # PDF'den veri çıkar
    extracted_data = extract_invoice_data_from_pdf(pdf_attachment)
    
    # ====== ÇIKARILAN TÜM VERİLERİ GÖSTER ======
    print("\n" + "="*80)
    print("📄 PDF'DEN ÇIKARILAN TÜM VERİLER:")
    print("="*80)
    import json
    print(json.dumps(extracted_data, indent=2, ensure_ascii=False, default=str))
    print("="*80 + "\n")
    
    # Invoice kaydı oluştur
    invoice = frappe.get_doc({
        "doctype": "Lieferando Invoice",
        
        # Temel bilgiler
        "invoice_number": extracted_data.get("invoice_number") or generate_temp_invoice_number(),
        "invoice_date": extracted_data.get("invoice_date") or frappe.utils.today(),
        "period_start": extracted_data.get("period_start"),
        "period_end": extracted_data.get("period_end"),
        "status": "Draft",
        
        # Lieferant (Supplier)
        "supplier_name": extracted_data.get("supplier_name") or "yd.yourdelivery GmbH",
        "supplier_email": extracted_data.get("supplier_email") or communication_doc.sender,
        "supplier_ust_idnr": extracted_data.get("supplier_ust_idnr"),
        "supplier_iban": extracted_data.get("supplier_iban"),
        
        # Kunde (Customer/Restaurant)
        "restaurant_name": extracted_data.get("restaurant_name"),
        "customer_number": extracted_data.get("customer_number"),
        "customer_company": extracted_data.get("customer_company"),
        "restaurant_address": extracted_data.get("restaurant_address"),
        "customer_bank_iban": extracted_data.get("customer_bank_iban"),
        
        # Bestellungen (Orders)
        "total_orders": extracted_data.get("total_orders") or 0,
        "total_revenue": extracted_data.get("total_revenue") or 0,
        "online_paid_orders": extracted_data.get("online_paid_orders") or 0,
        "online_paid_amount": extracted_data.get("online_paid_amount") or 0,
        
        # Einzelauflistung (Sadece 3 alan)
        "ausstehende_am_datum": extracted_data.get("invoice_date"),  # Tarih
        "ausstehende_onlinebezahlungen_betrag": extracted_data.get("outstanding_balance") or extracted_data.get("total_revenue") or 0,  # € 24,00
        "rechnungsausgleich_betrag": extracted_data.get("total_amount") or 0,  # € 9,33
        "auszahlung_gesamt": extracted_data.get("payout_amount") or 0,  # € 14,67
        
        # Gebühren (Fees)
        "service_fee_rate": extracted_data.get("service_fee_rate") or 30,
        "service_fee_amount": extracted_data.get("service_fee_amount") or 0,
        "admin_fee_amount": extracted_data.get("admin_fee_amount") or 0,
        
        # Beträge (Amounts)
        "subtotal": extracted_data.get("subtotal") or 0,
        "tax_rate": extracted_data.get("tax_rate") or 19,
        "tax_amount": extracted_data.get("tax_amount") or 0,
        "total_amount": extracted_data.get("total_amount") or 0,
        "paid_online_payments": extracted_data.get("paid_online_payments") or 0,
        "outstanding_amount": extracted_data.get("outstanding_amount") or 0,
        
        # Auszahlung (Payout)
        "payout_amount": extracted_data.get("payout_amount") or 0,
        "outstanding_balance": extracted_data.get("outstanding_balance") or 0,
        
        # Email metadata
        "email_subject": communication_doc.subject,
        "email_from": communication_doc.sender,
        "received_date": communication_doc.creation,
        "processed_date": frappe.utils.now(),
        "extraction_confidence": extracted_data.get("confidence", 50),
        "raw_text": extracted_data.get("raw_text", "")
    })
    
    # Order items alanını sonra ekle (boş olabilir)
    order_items = extracted_data.get("order_items", [])
    if order_items:
        invoice.order_items = order_items
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    print(f"✅ Invoice oluşturuldu: {invoice.name}")
    
    # PDF'i Invoice'a ekle
    attach_pdf_to_invoice(pdf_attachment, invoice.name)
    
    return invoice


def extract_invoice_data_from_pdf(pdf_attachment):
    """
    PDF'den fatura verilerini çıkar
    Basit regex tabanlı çıkarım (gelişmiş AI kullanılabilir)
    """
    try:
        # Frappe'de pypdf zaten yüklü, onu kullan
        from pypdf import PdfReader
        import io
        
        # PDF içeriğini oku
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_path = file_doc.get_full_path()
        
        print(f">>>>>> PDF dosyası okunuyor: {file_path}")
        
        # PDF'i aç
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PdfReader(pdf_file)
            
            print(f">>>>>> PDF sayfa sayısı: {len(pdf_reader.pages)}")
            
            # Tüm sayfalardan text çıkar
            full_text = ""
            for i, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                full_text += page_text
                print(f">>>>>> Sayfa {i+1} metin uzunluğu: {len(page_text)} karakter")
                
        print(f">>>>>> PDF'den toplam {len(full_text)} karakter metin çıkarıldı")
        
        # Eğer metin çok kısa ise, PDF scanned image olabilir
        if len(full_text.strip()) < 50:
            print(f"⚠️ UYARI: PDF'ten çok az metin çıkarıldı ({len(full_text)} karakter)")
            print(f"⚠️ PDF scanned image olabilir, OCR gerekebilir")
            print(f">>>>>> Çıkarılan metin önizleme: {full_text[:200]}")
        else:
            print(f">>>>>> PDF'den çıkarılan metin önizleme (ilk 500 karakter):")
            print(f"{full_text[:500]}")
            print(f"...")
        
        # Regex ile veri çıkar
        data = {
            "raw_text": full_text,
            "confidence": 60  # Varsayılan güven skoru
        }
        
        # Invoice Number - Lieferando özel: "Rechnungsnummer: 313935291"
        invoice_patterns = [
            r'Rechnungsnummer[\s:]*(\d+)',  # Lieferando format
            r'Rechnungsnummer[\s:]*([A-Z0-9\-]+)',  # Alternatif format
            r'Invoice\s*(?:Number|No|#)[\s:]*([A-Z0-9\-]+)',
            r'Rechnung\s*(?:Nr|#)[\s:]*([A-Z0-9\-]+)',
            r'Fatura\s*(?:No|#)[\s:]*([A-Z0-9\-]+)',
        ]
        for pattern in invoice_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE | re.MULTILINE)
            if match:
                data["invoice_number"] = match.group(1).strip()
                print(f"✅ Rechnungsnummer bulundu: {data['invoice_number']} (pattern: {pattern})")
                break
        if not data.get("invoice_number"):
            print(f"❌ Rechnungsnummer bulunamadı")
        
        # Date (çeşitli formatlar)
        date_patterns = [
            r'Datum[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'Date[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'Rechnungsdatum[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE | re.MULTILINE)
            if match:
                date_str = match.group(1)
                try:
                    # Tarih formatını parse et
                    data["invoice_date"] = parse_date(date_str)
                    print(f"✅ Invoice Date bulundu: {data['invoice_date']} (pattern: {pattern})")
                    break
                except Exception as e:
                    print(f"⚠️ Tarih parse edilemedi: {date_str}, hata: {e}")
                    pass
        if not data.get("invoice_date"):
            print(f"❌ Invoice Date bulunamadı")
        
        # IBAN (genel)
        iban_match = re.search(r'([A-Z]{2}\d{2}[\s]?[\d\s]{10,30})', full_text, re.IGNORECASE | re.MULTILINE)
        if iban_match:
            data["iban"] = iban_match.group(1).replace(' ', '')
            print(f"✅ IBAN bulundu: {data['iban']}")
        
        # === LIEFERANDO ÖZEL ALANLAR ===
        
        # Kundennummer: 13002774
        customer_num_match = re.search(r'Kundennummer[\s:]*(\d+)', full_text, re.IGNORECASE | re.MULTILINE)
        if customer_num_match:
            data["customer_number"] = customer_num_match.group(1)
            print(f"✅ Kundennummer bulundu: {data['customer_number']}")
        else:
            print(f"❌ Kundennummer bulunamadı")
        
        # Restaurant Name - "z.Hd. Restaurant Name" formatında
        restaurant_match = re.search(r'z\.Hd\.\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE | re.MULTILINE)
        if restaurant_match:
            data["restaurant_name"] = restaurant_match.group(1).strip()
            print(f"✅ Restaurant Name bulundu: {data['restaurant_name']}")
        else:
            # Alternatif: Direkt restaurant ismi araması
            restaurant_alt = re.search(r'Restaurant[\s:]+(.+?)(?:\n|Kundennummer|Rechnungsnummer)', full_text, re.IGNORECASE | re.MULTILINE)
            if restaurant_alt:
                data["restaurant_name"] = restaurant_alt.group(1).strip()
                print(f"✅ Restaurant Name (alternatif) bulundu: {data['restaurant_name']}")
            else:
                print(f"❌ Restaurant Name bulunamadı")
        
        # Zeitraum (Period): "05-10-2025 bis einschließlich 11-10-2025"
        period_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+bis\s+(?:einschließlich\s+)?(\d{2}-\d{2}-\d{4})', full_text, re.IGNORECASE | re.MULTILINE)
        if period_match:
            data["period_start"] = parse_date(period_match.group(1))
            data["period_end"] = parse_date(period_match.group(2))
            print(f"✅ Period bulundu: {data['period_start']} - {data['period_end']}")
        else:
            print(f"❌ Period bulunamadı")
        
        # Anzahl Bestellungen: "1 Bestellung"
        orders_match = re.search(r'(\d+)\s+Bestellung', full_text, re.IGNORECASE | re.MULTILINE)
        if orders_match:
            data["total_orders"] = int(orders_match.group(1))
            data["online_paid_orders"] = int(orders_match.group(1))
            print(f"✅ Total Orders bulundu: {data['total_orders']}")
        else:
            print(f"❌ Total Orders bulunamadı")
        
        # Umsatz: "Ihr Umsatz in der Zeit vom 05-10-2025 bis einschließlich 11-10-2025: € 24,00."
        revenue_match = re.search(r'Ihr Umsatz in der Zeit[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if revenue_match:
            amount_str = revenue_match.group(1).replace(',', '.').rstrip('.')
            data["total_revenue"] = float(amount_str)
            data["online_paid_amount"] = float(amount_str)
            print(f"✅ Total Revenue bulundu: €{data['total_revenue']}")
        else:
            # Alternatif: "Gesamt 1 Bestellung im Wert von € 24,00"
            gesamt_match = re.search(r'Gesamt\s+\d+\s+Bestellung[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
            if gesamt_match:
                amount_str = gesamt_match.group(1).replace(',', '.').rstrip('.')
                data["total_revenue"] = float(amount_str)
                data["online_paid_amount"] = float(amount_str)
                print(f"✅ Total Revenue (alternatif) bulundu: €{data['total_revenue']}")
            else:
                print(f"❌ Total Revenue bulunamadı")
        
        # Servicegebühr: "Servicegebühr: 30,00% von € 24,00 € 7,20"
        service_fee_match = re.search(r'Servicegebühr:\s*([\d,\.]+)%[^€]*€\s*[\d,\.]+\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if service_fee_match:
            data["service_fee_rate"] = float(service_fee_match.group(1).replace(',', '.'))
            amount_str = service_fee_match.group(2).replace(',', '.').rstrip('.')
            data["service_fee_amount"] = float(amount_str)
            print(f"✅ Service Fee bulundu: {data['service_fee_rate']}% = €{data['service_fee_amount']}")
        else:
            print(f"❌ Service Fee bulunamadı")
        
        # Verwaltungsgebühr: İkinci satırdaki "Servicegebühr: € 0,64 x 1 € 0,64"
        # "Verwaltungsgebühr" başlığından SONRA gelen satırdaki pattern
        admin_fee_match = re.search(r'Verwaltungsgebühr.*?\n\s*Servicegebühr:\s*€\s*([\d,\.]+)\s+x\s+\d+', full_text, re.DOTALL | re.IGNORECASE)
        if admin_fee_match:
            amount_str = admin_fee_match.group(1).replace(',', '.').rstrip('.')
            data["admin_fee_amount"] = float(amount_str)
            print(f"✅ Admin Fee bulundu: €{data['admin_fee_amount']}")
        else:
            # Alternatif: Direkt "Verwaltungsgebühr: € X,XX"
            admin_alt = re.search(r'Verwaltungsgebühr[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
            if admin_alt:
                amount_str = admin_alt.group(1).replace(',', '.').rstrip('.')
                data["admin_fee_amount"] = float(amount_str)
                print(f"✅ Admin Fee (alternatif) bulundu: €{data['admin_fee_amount']}")
            else:
                print(f"❌ Admin Fee bulunamadı")
        
        # Zwischensumme: "€ 7,84"
        subtotal_match = re.search(r'Zwischensumme\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if subtotal_match:
            amount_str = subtotal_match.group(1).replace(',', '.').rstrip('.')
            data["subtotal"] = float(amount_str)
            print(f"✅ Subtotal bulundu: €{data['subtotal']}")
        else:
            print(f"❌ Subtotal bulunamadı")
        
        # MwSt.: "MwSt. (19% von € 7,84) € 1,49"
        tax_match = re.search(r'MwSt\.\s*\((\d+)%[^€]*€\s*[\d,\.]+\)\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if tax_match:
            data["tax_rate"] = float(tax_match.group(1))
            tax_amount_str = tax_match.group(2).replace(',', '.').rstrip('.')
            data["tax_amount"] = float(tax_amount_str)
            print(f"✅ Tax bulundu: {data['tax_rate']}% = €{data['tax_amount']}")
        else:
            print(f"❌ Tax bulunamadı")
        
        # Gesamtbetrag: "Gesamtbetrag dieser Rechnung € 9,33"
        total_match = re.search(r'Gesamtbetrag dieser Rechnung\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if total_match:
            amount_str = total_match.group(1).replace(',', '.').rstrip('.')
            data["total_amount"] = float(amount_str)
            print(f"✅ Total Amount bulundu: €{data['total_amount']}")
        else:
            # Alternatif pattern
            total_alt = re.search(r'GESAMTBETRAG[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
            if total_alt:
                amount_str = total_alt.group(1).replace(',', '.').rstrip('.')
                data["total_amount"] = float(amount_str)
                print(f"✅ Total Amount (alternatif) bulundu: €{data['total_amount']}")
            else:
                print(f"❌ Total Amount bulunamadı")
        
        # Verrechnet mit Online-Zahlungen: "€ 9,33"
        paid_match = re.search(r'Verrechnet mit eingegangenen Onlinebezahlungen\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if paid_match:
            amount_str = paid_match.group(1).replace(',', '.').rstrip('.')
            data["paid_online_payments"] = float(amount_str)
            print(f"✅ Paid Online Payments bulundu: €{data['paid_online_payments']}")
        else:
            print(f"❌ Paid Online Payments bulunamadı")
        
        # Offener Rechnungsbetrag: "€ 0,00"
        outstanding_match = re.search(r'Offener Rechnungsbetrag\s*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if outstanding_match:
            amount_str = outstanding_match.group(1).replace(',', '.').rstrip('.')
            data["outstanding_amount"] = float(amount_str)
            print(f"✅ Outstanding Amount bulundu: €{data['outstanding_amount']}")
        else:
            print(f"❌ Outstanding Amount bulunamadı")
        
        # Ausstehende Onlinebezahlungen (Einzelauflistung): "Ausstehende Onlinebezahlungen am 12-10-2025 ** € 24,00"
        ausstehende_match = re.search(r'Ausstehende Onlinebezahlungen am[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if ausstehende_match:
            amount_str = ausstehende_match.group(1).replace(',', '.').rstrip('.')
            data["outstanding_balance"] = float(amount_str)
            print(f"✅ Outstanding Balance bulundu: €{data['outstanding_balance']}")
        else:
            print(f"❌ Outstanding Balance bulunamadı")
        
        # Auszahlung Gesamt: "€ 14,67" (Einzelauflistung'daki toplam)
        # Pattern: "GmbH" ile "Datum" arasındaki son € değeri
        auszahlung_gesamt_match = re.search(r'COLLECTIVE GmbH[^€]*€\s*([\d,\.]+)\s*Datum', full_text, re.DOTALL | re.IGNORECASE)
        if auszahlung_gesamt_match:
            amount_str = auszahlung_gesamt_match.group(1).replace(',', '.').rstrip('.')
            data["payout_amount"] = float(amount_str)
            print(f"✅ Payout Amount bulundu: €{data['payout_amount']}")
        else:
            # Alternatif: "Auszahlung" veya "GESAMTAUSZAHLUNG" pattern'i
            auszahlung_alt = re.search(r'(?:GESAMTAUSZAHLUNG|Auszahlung)[^€]*€\s*([\d,\.]+)', full_text, re.IGNORECASE | re.MULTILINE)
            if auszahlung_alt:
                amount_str = auszahlung_alt.group(1).replace(',', '.').rstrip('.')
                data["payout_amount"] = float(amount_str)
                print(f"✅ Payout Amount (alternatif) bulundu: €{data['payout_amount']}")
            else:
                print(f"❌ Payout Amount bulunamadı")
        
        # Customer Company: "z.Hd. CC CULINARY COLLECTIVE GmbH"
        company_match = re.search(r'z\.Hd\.\s+(.+?GmbH)', full_text, re.IGNORECASE | re.MULTILINE)
        if company_match:
            data["customer_company"] = company_match.group(1).strip()
            print(f"✅ Customer Company bulundu: {data['customer_company']}")
        else:
            print(f"❌ Customer Company bulunamadı")
        
        # Customer Bank IBAN
        cust_iban_match = re.search(r'Bankkonto\s+(DE[\d\s]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if cust_iban_match:
            data["customer_bank_iban"] = cust_iban_match.group(1).replace(' ', '')
            print(f"✅ Customer Bank IBAN bulundu: {data['customer_bank_iban']}")
        else:
            print(f"❌ Customer Bank IBAN bulunamadı")
        
        # Supplier IBAN
        supp_iban_match = re.search(r'IBAN:\s+(DE[\d\s]+)', full_text, re.IGNORECASE | re.MULTILINE)
        if supp_iban_match:
            data["supplier_iban"] = supp_iban_match.group(1).replace(' ', '')
            print(f"✅ Supplier IBAN bulundu: {data['supplier_iban']}")
        else:
            print(f"❌ Supplier IBAN bulunamadı")
        
        # Supplier USt-IdNr
        ust_match = re.search(r'USt\.-IdNr\.\s+(DE\d+)', full_text, re.IGNORECASE | re.MULTILINE)
        if ust_match:
            data["supplier_ust_idnr"] = ust_match.group(1)
            print(f"✅ Supplier USt-IdNr bulundu: {data['supplier_ust_idnr']}")
        else:
            print(f"❌ Supplier USt-IdNr bulunamadı")
        
        # Çıkarılan verilerin özeti
        print(f"\n{'='*80}")
        print(f"📊 ÇIKARILAN VERİLER ÖZETİ:")
        print(f"{'='*80}")
        print(f"✅ Rechnungsnummer: {data.get('invoice_number', 'BULUNAMADI')}")
        print(f"✅ Invoice Date: {data.get('invoice_date', 'BULUNAMADI')}")
        print(f"✅ Kundennummer: {data.get('customer_number', 'BULUNAMADI')}")
        print(f"✅ Restaurant Name: {data.get('restaurant_name', 'BULUNAMADI')}")
        print(f"✅ Total Revenue: €{data.get('total_revenue', 0)}")
        print(f"✅ Total Amount: €{data.get('total_amount', 0)}")
        print(f"✅ Auszahlung: €{data.get('payout_amount', 0)}")
        print(f"✅ Service Fee: {data.get('service_fee_rate', 0)}% = €{data.get('service_fee_amount', 0)}")
        print(f"✅ Tax Rate: {data.get('tax_rate', 0)}% = €{data.get('tax_amount', 0)}")
        print(f"{'='*80}\n")
        
        return data
        
    except ImportError as e:
        print(f"⚠️ pypdf yüklü değil: {str(e)}")
        print(f"⚠️ Frappe'de pypdf olmalı, kontrol edin")
        frappe.log_error(
            title="PDF Library Import Error",
            message=f"pypdf import edilemedi: {str(e)}"
        )
        return {"raw_text": "", "confidence": 0}
    
    except Exception as e:
        print(f"❌ PDF OKUMA HATASI: {str(e)}")
        print(f"❌ Traceback: {frappe.get_traceback()}")
        frappe.log_error(
            title="PDF Extraction Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return {"raw_text": "", "confidence": 0}


def attach_pdf_to_invoice(pdf_attachment, invoice_name):
    """
    PDF'i Invoice kaydına attach et
    """
    try:
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        
        # Yeni File dokümenti oluştur (kopyala)
        new_file = frappe.copy_doc(file_doc)
        new_file.attached_to_doctype = "Lieferando Invoice"
        new_file.attached_to_name = invoice_name
        new_file.save(ignore_permissions=True)
        
        # Invoice'ın pdf_file alanını güncelle
        frappe.db.set_value("Lieferando Invoice", invoice_name, "pdf_file", new_file.file_url)
        
        print(f"✅ PDF attached: {pdf_attachment.file_name}")
        
    except Exception as e:
        frappe.log_error(
            title="PDF Attachment Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def generate_temp_invoice_number():
    """
    Geçici fatura numarası oluştur
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"TEMP-{timestamp}"


def parse_date(date_str):
    """
    Çeşitli tarih formatlarını parse et
    """
    formats = [
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d.%m.%y",
        "%d/%m/%y",
    ]
    
    for fmt in formats:
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            return parsed_date.strftime("%Y-%m-%d")
        except:
            continue
    
    # Parse edilemezse bugünün tarihi
    return frappe.utils.today()

