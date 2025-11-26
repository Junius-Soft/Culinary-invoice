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
        
        # Bu email zaten işlendi mi kontrol et (Lieferando + Wolt)
        duplicate_filters = {
            "email_from": doc.sender,
            "email_subject": doc.subject,
            "received_date": doc.creation
        }
        existing_lieferando = frappe.db.exists("Lieferando Invoice", duplicate_filters)
        existing_wolt = frappe.db.exists("Wolt Invoice", duplicate_filters)
        
        if existing_lieferando or existing_wolt:
            print(f">>>>>> Email zaten işlenmiş, atlandı: {doc.subject}")
            return
        
        # Subject kontrolü - invoice/fatura içeriyor mu?
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
    
    extracted_data = extract_invoice_data_from_pdf(pdf_attachment)
    
    # ====== ÇIKARILAN TÜM VERİLERİ GÖSTER ======
    print("\n" + "="*80)
    print("📄 PDF'DEN ÇIKARILAN TÜM VERİLER:")
    print("="*80)
    import json
    print(json.dumps(extracted_data, indent=2, ensure_ascii=False, default=str))
    print("="*80 + "\n")
    
    platform = extracted_data.get("platform") or "lieferando"
    
    if platform == "wolt":
        return create_wolt_invoice_doc(communication_doc, pdf_attachment, extracted_data)
    
    return create_lieferando_invoice_doc(communication_doc, pdf_attachment, extracted_data)


def create_lieferando_invoice_doc(communication_doc, pdf_attachment, extracted_data):
    """
    Lieferando Invoice kaydı oluştur
    """
    invoice_number = extracted_data.get("invoice_number")
    if invoice_number and frappe.db.exists("Lieferando Invoice", {"invoice_number": invoice_number}):
        print(f">>>>>> {invoice_number} zaten mevcut, atlandı.")
        return
    
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
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    print(f"✅ Lieferando Invoice oluşturuldu: {invoice.name}")
    attach_pdf_to_invoice(pdf_attachment, invoice.name, "Lieferando Invoice")
    return invoice


def create_wolt_invoice_doc(communication_doc, pdf_attachment, extracted_data):
    """
    Wolt Invoice kaydı oluştur
    """
    invoice_number = extracted_data.get("invoice_number")
    if invoice_number and frappe.db.exists("Wolt Invoice", {"invoice_number": invoice_number}):
        print(f">>>>>> {invoice_number} zaten mevcut (Wolt), atlandı.")
        return
    
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
    
    invoice.insert(ignore_permissions=True, ignore_mandatory=True)
    print(f"✅ Wolt Invoice oluşturuldu: {invoice.name}")
    attach_pdf_to_invoice(pdf_attachment, invoice.name, "Wolt Invoice")
    return invoice


def extract_invoice_data_from_pdf(pdf_attachment):
    """
    PDF'den fatura verilerini çıkar
    Basit regex tabanlı çıkarım (gelişmiş AI kullanılabilir)
    """
    try:
        import PyPDF2
        import io
        
        # PDF içeriğini oku
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        file_path = file_doc.get_full_path()
        
        # PDF'i aç
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            # Tüm sayfalardan text çıkar
            full_text = ""
            for page in pdf_reader.pages:
                full_text += page.extract_text()
                
        print(f">>>>>> PDF'den çıkarılan metin: {full_text}")
        print(f">>>>>> PDF'den {len(full_text)} karakter metin çıkarıldı")
        
        # Regex ile veri çıkar
        data = {
            "raw_text": full_text,
            "confidence": 60  # Varsayılan güven skoru
        }
        
        # Invoice Number - Lieferando özel: "Rechnungsnummer: 313935291"
        invoice_patterns = [
            r'Rechnungsnummer[\s:]*([A-Z0-9\/\-]+)',  # Genel format
            r'Invoice\s*(?:Number|No|#)[\s:]*([A-Z0-9\-]+)',
            r'Rechnung\s*(?:Nr|#)[\s:]*([A-Z0-9\-]+)',
            r'Fatura\s*(?:No|#)[\s:]*([A-Z0-9\-]+)',
        ]
        for pattern in invoice_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                data["invoice_number"] = match.group(1).strip()
                break
        
        # Date (çeşitli formatlar)
        date_patterns = [
            r'Date[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'Datum[\s:]*(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
            r'(\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, full_text)
            if match:
                date_str = match.group(1)
                try:
                    # Tarih formatını parse et
                    data["invoice_date"] = parse_date(date_str)
                    break
                except:
                    pass
        
        # Total Amount (çeşitli formatlar)
        total_patterns = [
            r'Total[\s:]*[€$£]?\s*([\d,\.]+)',
            r'Gesamt[\s:]*[€$£]?\s*([\d,\.]+)',
            r'Toplam[\s:]*[€$£]?\s*([\d,\.]+)',
            r'[€$£]\s*([\d,\.]+)',
        ]
        for pattern in total_patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            if matches:
                # En büyük sayıyı al (genellikle toplam tutar)
                amounts = []
                for m in matches:
                    try:
                        amount = float(m.replace(',', ''))
                        amounts.append(amount)
                    except:
                        pass
                if amounts:
                    data["total_amount"] = max(amounts)
                    break
        
        # IBAN
        iban_match = re.search(r'([A-Z]{2}\d{2}[\s]?[\d\s]{10,30})', full_text)
        if iban_match:
            data["iban"] = iban_match.group(1).replace(' ', '')
        
        platform = detect_invoice_platform(full_text)
        data["platform"] = platform or "lieferando"
        
        if platform == "wolt":
            data.update(extract_wolt_fields(full_text))
        else:
            data.update(extract_lieferando_fields(full_text))
        
        print(f">>>>>> Çıkarılan veriler:")
        print(f"       - Platform: {data.get('platform')}")
        print(f"       - Rechnungsnummer: {data.get('invoice_number')}")
        print(f"       - Gesamtbetrag: €{data.get('total_amount')}")
        print(f"       - Tüm veriler: {data}")
        
        return data
        
    except ImportError as e:
        print(f"⚠️ PyPDF2 yüklü değil: {str(e)}")
        return {"raw_text": "", "confidence": 0}
    
    except Exception as e:
        print(f"❌ PDF OKUMA HATASI: {str(e)}")
        print(f"❌ Traceback: {frappe.get_traceback()}")
        frappe.log_error(
            title="PDF Extraction Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return {"raw_text": "", "confidence": 0}


def detect_invoice_platform(full_text: str) -> str:
    normalized = (full_text or "").lower()
    if "wolt" in normalized and "lieferando" not in normalized:
        return "wolt"
    if "lieferando" in normalized or "yourdelivery" in normalized or "takeaway" in normalized:
        return "lieferando"
    return "unknown"


def extract_lieferando_fields(full_text: str) -> dict:
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
    data = {"platform": "wolt"}
    clean_text = (full_text or "").replace("|", " ")
    
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


def parse_decimal(value: str | None):
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    clean = clean.replace("€", "").replace("%", "")
    clean = clean.replace("−", "-")
    clean = clean.replace(" ", "")
    # Remove thousand separators but keep decimal part
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    else:
        clean = clean.replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def attach_pdf_to_invoice(pdf_attachment, invoice_name, target_doctype):
    """
    PDF'i Invoice kaydına attach et
    """
    try:
        file_doc = frappe.get_doc("File", pdf_attachment.name)
        
        # File içeriğini oku
        file_content = file_doc.get_content()
        
        # Yeni File dokümanı oluştur (içerikle birlikte)
        # PDF'leri public yapıyoruz ki direkt erişilebilsinler
        new_file = frappe.get_doc({
            "doctype": "File",
            "file_name": file_doc.file_name,
            "attached_to_doctype": target_doctype,
            "attached_to_name": invoice_name,
            "attached_to_field": "pdf_file",  # Hangi alana attach edildiğini belirt
            "is_private": 0,  # Public yap - böylece görüntülenebilir
            "content": file_content,
            "folder": "Home/Attachments"
        })
        new_file.flags.ignore_permissions = True
        new_file.insert()
        
        # Invoice'ın pdf_file alanını güncelle
        # URL'i relative olarak kaydet (Frappe UI'da attach field zaten absolute URL'e çevirecek)
        frappe.db.set_value(target_doctype, invoice_name, "pdf_file", new_file.file_url)
        frappe.db.commit()
        
        print(f"✅ PDF attached: {pdf_attachment.file_name} -> {new_file.file_url}")
        
    except Exception as e:
        frappe.log_error(
            title="PDF Attachment Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        print(f"❌ PDF attach hatası: {str(e)}")


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
