frappe.listview_settings['Wolt Invoice'] = {
    onload: function(listview) {
        // Batch AI Validation butonu (toolbar)
        listview.page.add_button(__("Batch AI Validation"), function() {
            show_batch_validation_dialog("Wolt Invoice", listview);
        }, {
            btn_class: "btn-primary",
            btn_size: "btn-sm"
        });
    },
    formatters: {
        ai_validation_confidence: function(value) {
            if (!value) return '';
            let score = parseFloat(value);
            let color = score >= 90 ? 'green' : (score > 0 ? 'red' : 'gray');
            return `<span style="color: ${color}; font-weight: bold;">${score.toFixed(1)}%</span>`;
        }
    }
};

function show_batch_validation_dialog(doctype, listview) {
    let dialog = new frappe.ui.Dialog({
        title: __('Batch AI Validation'),
        fields: [
            {
                label: __('Progress'),
                fieldname: 'progress_section',
                fieldtype: 'Section Break'
            },
            {
                fieldname: 'progress_html',
                fieldtype: 'HTML',
                options: '<div style="padding: 10px; text-align: center; color: #999;">Hazır...</div>'
            }
        ],
        primary_action_label: __('Start Validation'),
        primary_action: function() {
            // Seçili invoice'ları butona basıldığında kontrol et (güncel seçimi almak için)
            let checked_items = listview.get_checked_items(true); // Sadece name'leri al
            
            // Seçim kontrolü - eğer hiçbir şey seçilmemişse uyar
            if (!checked_items || checked_items.length === 0) {
                frappe.msgprint({
                    title: __('Uyarı'),
                    message: __('Lütfen validasyon yapmak istediğiniz invoice\'ları seçin.'),
                    indicator: 'orange'
                });
                return;
            }
            
            start_batch_validation(doctype, dialog, listview, checked_items);
        }
    });
    
    dialog.show();
}

function start_batch_validation(doctype, dialog, listview, selected_items) {
    // Disable start button
    dialog.get_primary_btn().prop('disabled', true);
    
    // Seçili invoice'ları kullan (seçim kontrolü zaten dialog'da yapıldı)
    let invoices = selected_items.map(function(name) {
        return { name: name, invoice_number: name }; // Basit format
    });
    
    // Update progress HTML
    let progress_html = dialog.fields_dict.progress_html;
    if (progress_html) {
        progress_html.$wrapper.html(`<div style="padding: 10px; text-align: center; color: #666;"><strong>${selected_items.length} seçili invoice validasyon edilecek...</strong></div>`);
    }
    
    process_invoices_sequentially(doctype, invoices, 0, invoices.length, dialog, listview);
}

function process_invoices_sequentially(doctype, invoices, current_index, total_count, dialog, listview) {
    if (current_index >= invoices.length) {
        // All done - refresh list view
        let progress_html = dialog.fields_dict.progress_html;
        if (progress_html) {
            progress_html.$wrapper.html('<div style="padding: 10px; text-align: center; color: green;"><strong>✅ Tamamlandı! List view yenileniyor...</strong></div>');
        }
        dialog.get_primary_btn().prop('disabled', false);
        
        // Close dialog and refresh list view after a short delay
        setTimeout(function() {
            dialog.hide();
            if (listview) {
                listview.refresh();
            } else {
                frappe.set_route('List', doctype);
            }
            frappe.show_alert({
                message: `${total_count} invoice validasyonu tamamlandı. Sonuçlar list view'da görüntüleniyor.`,
                indicator: 'green'
            }, 5);
        }, 500);
        return;
    }
    
    let invoice = invoices[current_index];
    let progress = ((current_index / total_count) * 100).toFixed(1);
    
    // Update progress HTML
    let progress_html = dialog.fields_dict.progress_html;
    if (progress_html) {
        progress_html.$wrapper.html(`<div style="padding: 10px; text-align: center;"><strong>İşleniyor: ${current_index + 1}/${total_count} (${progress}%)</strong><br><small>${invoice.invoice_number || invoice.name}</small></div>`);
    }
    
    // Validate this invoice
    frappe.call({
        method: 'invoice.api.invoice_ai_validation.recheck_invoice_with_ai',
        args: {
            doctype: doctype,
            name: invoice.name,
            show_message: false  // Batch validation için mesaj gösterme
        },
        callback: function(r) {
            // Process next invoice immediately
            setTimeout(function() {
                process_invoices_sequentially(doctype, invoices, current_index + 1, total_count, dialog, listview);
            }, 100);
        },
        error: function(r) {
            // Continue with next invoice even on error
            setTimeout(function() {
                process_invoices_sequentially(doctype, invoices, current_index + 1, total_count, dialog, listview);
            }, 100);
        }
    });
}



