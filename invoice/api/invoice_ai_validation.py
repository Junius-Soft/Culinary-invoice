import frappe
import json
import os
import base64

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = frappe.logger("invoice.ai_validation", allow_site=frappe.local.site)

def get_openai_client():
    """OpenAI client oluştur"""
    if OpenAI is None:
        frappe.throw("OpenAI paketi yüklü değil. Lütfen 'pip install openai' komutu ile yükleyin.")
    
    api_key = frappe.conf.get("openai_api_key") or os.getenv("OPENAI_API_KEY")

    if not api_key:
        frappe.throw("OpenAI API key bulunamadı. Lütfen 'openai_api_key' site config'e ekleyin veya OPENAI_API_KEY environment variable'ı ayarlayın.")
    return OpenAI(api_key=api_key)

def get_pdf_file_doc(invoice_doc):
    """Invoice'ın PDF File doc'unu bul"""
    if not invoice_doc.pdf_file:
        frappe.throw(f"PDF dosyası bulunamadı (Invoice: {invoice_doc.name})")
    
    file_url = invoice_doc.pdf_file
    if file_url.startswith("/"):
        file_url = file_url[1:]
    
    file_docs = frappe.get_all("File", 
        filters={"file_url": file_url},
        limit=1
    )
    
    if not file_docs:
     
        file_docs = frappe.get_all("File",
            filters={
                "attached_to_doctype": invoice_doc.doctype,
                "attached_to_name": invoice_doc.name,
                "attached_to_field": "pdf_file"
            },
            limit=1
        )
    
    if not file_docs:
        frappe.throw(f"PDF File doc bulunamadı (Invoice: {invoice_doc.name}, URL: {invoice_doc.pdf_file})")
    
    return frappe.get_doc("File", file_docs[0].name)

def prepare_invoice_data_for_ai(invoice_doc):
    """Invoice DocType verilerini AI'ya göndermek için hazırla"""
    doctype = invoice_doc.doctype
    data = {}
    meta = frappe.get_meta(doctype)
    
    default_only_fields = ['supplier_email', 'supplier_phone']  
    
    for field in meta.fields:
        fieldname = field.fieldname
        if fieldname in ['name', 'doctype', 'owner', 'creation', 'modified', 'modified_by']:
            continue
        if field.fieldtype in ['Section Break', 'Column Break', 'Tab Break']:
            continue
        if field.fieldtype == 'Attach':
            continue 
        if field.hidden:
            continue
        
        value = invoice_doc.get(fieldname)
        if value is not None and value != "":
        
            if fieldname in default_only_fields and field.default and str(value) == str(field.default):
                data[fieldname] = f"{str(value)} (default - PDF'te olmayabilir)"
            else:
                data[fieldname] = str(value) if not isinstance(value, (dict, list)) else json.dumps(value)
    
    return data

def validate_invoice_with_ai(invoice_doctype, invoice_name):
    """Invoice'ı OpenAI ile doğrula"""
    try:
        invoice_doc = frappe.get_doc(invoice_doctype, invoice_name)
        
        # Invoice verilerini hazırla
        invoice_data = prepare_invoice_data_for_ai(invoice_doc)
        
        # OpenAI client
        client = get_openai_client()
        
        # Prompt hazırla (English for AI, results will be in Turkish)
        prompt = f"""You are an invoice validation expert. Compare the invoice data in JSON format below with the PDF content and perform accuracy validation.

Invoice DocType: {invoice_doctype}
Invoice Number: {invoice_doc.invoice_number}

Invoice data (extracted from DocType):
{json.dumps(invoice_data, indent=2, ensure_ascii=False)}

Task:
1. Analyze the PDF content
2. Extract important fields from the PDF (invoice number, dates, amounts, company info, etc.)
3. Compare PDF data with DocType data
4. Identify missing, incorrect, or mismatched fields
5. Provide an overall accuracy assessment

IMPORTANT COMPARISON RULES:
- For numerical values (amount, rate, etc.): Perform float comparison. For example, "2.70" and "2.7" or "9.00" and "9.0" should be considered the same. Ignore minor rounding differences (less than 0.01).
- Amount vs Rate/Percentage: ATTENTION! Fields ending with "_amount" are CURRENCY AMOUNTS (e.g., 2.70), fields ending with "_rate" or "_percent" are PERCENTAGES (e.g., 30). Do not confuse them! "admin_fee_amount" is an amount, "service_fee_rate" is a percentage. If the PDF shows "Admin Fee: €0.64" as an amount, compare it with the "_amount" field, not with a percentage.
- For date fields: Format differences are not important (e.g., "14-12-2025" and "2025-12-14" are the same).
- For text fields: Case differences and leading/trailing spaces are not important.
- Default values: If a field value is marked with "(default - may not be in PDF)", do NOT add this field to the "missing_fields" list if it's not in the PDF! These are system default values and are not mandatory in the PDF.

Response format (JSON):
{{
    "status": "Valid" | "Issues Found" | "Error",
    "confidence": 0.0-1.0 (accuracy confidence),
    "summary": "Short summary in Turkish (max 200 characters)",
    "details": {{
        "missing_fields": ["field1", "field2"],  // In PDF but not in DocType
        "incorrect_fields": ["field1", "field2"],  // Actually mismatched fields (numerical difference >0.01 or text difference)
        "extras_in_pdf": ["field1", "field2"],  // In PDF but unexpected in DocType
        "field_comparisons": [
            {{
                "field": "invoice_number",
                "pdf_value": "...",
                "doctype_value": "...",
                "match": true/false  // For numerical values, perform float comparison
            }}
        ]
    }},
    "recommendations": ["recommendation1 in Turkish", "recommendation2 in Turkish"]
}}

IMPORTANT: Provide response in JSON format only, no additional text. The summary and recommendations should be in Turkish."""

        # PDF raw text'i al (PDF gönderimi yerine metin kullanıyoruz; API PDF'i image olarak kabul etmiyor)
        raw_text = invoice_doc.get("raw_text", "")
        if not raw_text:
            frappe.throw("PDF raw text bulunamadı. Önce fatura işlenmiş olmalı.")
        
        # OpenAI API çağrısı - PDF text'i ile analiz
        messages = [
            {
                "role": "system",
                "content": "You are an invoice validation expert. You compare PDF text with DocType data and perform accuracy analysis. Provide responses in Turkish for summary and recommendations fields, but use English for technical terms and field names."
            },
            {
                "role": "user",
                "content": f"""{prompt}

PDF Text (Raw):
{raw_text[:15000]}  # Max 15000 chars
"""
            }
        ]
        
        response = client.chat.completions.create(
            model="gpt-4o",  # veya "gpt-4-turbo"
            messages=messages,
            temperature=0.3,
            max_tokens=2000
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # JSON'u parse et
        try:
            # Eğer yanıt ```json ... ``` formatındaysa temizle
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            validation_result = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"AI yanıtı parse edilemedi: {response_text}")
            frappe.throw(f"AI yanıtı parse edilemedi: {str(e)}")
        
        # Sonuçları invoice'a kaydet
        update_ai_validation_fields(invoice_doc, validation_result)
        
        return validation_result
        
    except Exception as e:
        logger.error(f"AI validation hatası: {str(e)}\n{frappe.get_traceback()}")
        frappe.log_error(
            title="AI Validation Error",
            message=f"Invoice: {invoice_doctype} / {invoice_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )
        
        # Hata durumunda status'u güncelle (submit edilmiş invoice'larda da çalışması için set_value kullan)
        try:
            frappe.db.set_value(invoice_doctype, invoice_name, {
                "ai_validation_status": "Error",
                "ai_validation_summary": f"Error: {str(e)}"[:200],
                "ai_validation_date": frappe.utils.now()
            }, update_modified=False)
            frappe.db.commit()
        except Exception as update_error:
            logger.error(f"Error field update hatası: {str(update_error)}")
        
        frappe.throw(f"AI validation hatası: {str(e)}")

def update_ai_validation_fields(invoice_doc, validation_result):
    """AI validation sonuçlarını invoice alanlarına yaz"""
    status = validation_result.get("status", "Error")
    summary = validation_result.get("summary", "")[:200]  # Max 200 karakter
    confidence = (validation_result.get("confidence", 0) * 100) if validation_result.get("confidence") else None
    result_json = json.dumps(validation_result, indent=2, ensure_ascii=False)
    validation_date = frappe.utils.now()
    
    # Submit edilmiş invoice'larda da çalışması için set_value kullan
    frappe.db.set_value(invoice_doc.doctype, invoice_doc.name, {
        "ai_validation_status": status,
        "ai_validation_summary": summary,
        "ai_validation_confidence": confidence,
        "ai_validation_result": result_json,
        "ai_validation_date": validation_date
    }, update_modified=False)
    frappe.db.commit()

@frappe.whitelist()
def recheck_invoice_with_ai(doctype, name, show_message=True):
    """Server method: Invoice'ı AI ile tekrar kontrol et
    
    Args:
        doctype: Invoice doctype
        name: Invoice name
        show_message: If True, show success message (default: True)
    """
    try:
        result = validate_invoice_with_ai(doctype, name)
        if show_message:
            frappe.msgprint(
                f"AI Validation tamamlandı: {result.get('status')} (Confidence: {result.get('confidence', 0)*100:.1f}%)",
                indicator="green" if result.get("status") == "Valid" else "orange"
            )
        return result
    except Exception as e:
        if show_message:
            frappe.msgprint(f"Hata: {str(e)}", indicator="red")
        frappe.throw(str(e))

