// Copyright (c) 2025, invoice and contributors
// For license information, please see license.txt

frappe.ui.form.on("Wolt Invoice", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Recheck with AI"), function() {
				frappe.call({
					method: "invoice.api.invoice_ai_validation.recheck_invoice_with_ai",
					args: {
						doctype: frm.doctype,
						name: frm.doc.name
					},
					freeze: true,
					freeze_message: __("AI ile kontrol ediliyor..."),
					callback: function(r) {
						if (r.message) {
							frm.reload_doc();
							frappe.show_alert({
								message: __("AI Validation tamamlandı"),
								indicator: "green"
							}, 3);
						}
					},
					error: function(r) {
						frappe.show_alert({
							message: __("Hata: {0}", [r.message || "Bilinmeyen hata"]),
							indicator: "red"
						}, 5);
					}
				});
			}, __("Actions"));
		}
	},
});
