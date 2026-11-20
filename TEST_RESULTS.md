# Test results – SDJ Mobile Care

**Result: 87 passed in 111.38s** · run on 19 Nov 2026 · Python 3.13.18 · PySide6 6.12.0 · SQLAlchemy 2.1.9 · ReportLab 5.0.1 · Matplotlib 3.11.2 · OpenPyXL 3.1.5

Run them yourself: `set QT_QPA_PLATFORM=offscreen` then `python -m pytest -v`.


## Backup, restore and recovery  (`test_backup_recovery.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_backup_and_restore_roundtrip` | Backup then restore returns the data to the backup state (safety copy kept) |
| ✅ PASSED | `test_restore_rejects_foreign_files` | Restore refuses files that are not SDJ databases |
| ✅ PASSED | `test_corruption_detected_and_recovered_from_backup` | Database recovery: corrupted file detected, latest good backup restored |
| ✅ PASSED | `test_failed_transaction_leaves_no_partial_data` | Atomic transactions: a failing bill leaves no partial stock deduction |
| ✅ PASSED | `test_auto_backup_cleanup` | Old backups cleaned up to the configured number |

## Returns, exchanges, cancellations, stock, credit, duplicates, GST, IMEI, locking  (`test_edge_cases.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_insufficient_stock_blocks_sale_and_changes_nothing` | Insufficient stock blocks the sale (single and multi-line); nothing is changed |
| ✅ PASSED | `test_negative_stock_needs_authorised_adjustment` | Negative stock prevented; only an audited authorised adjustment adds stock |
| ✅ PASSED | `test_stock_movement_cannot_be_applied_twice` | The same sale line can never deduct stock twice (database constraint) |
| ✅ PASSED | `test_duplicate_bill_with_same_client_ref_is_rejected` | Double-saving the same bill is rejected; stock and revenue counted once |
| ✅ PASSED | `test_duplicate_supplier_invoice_rejected` | The same supplier invoice cannot be entered twice |
| ✅ PASSED | `test_cancelled_bill_restores_stock_and_voids_payment` | Cancelled bill: stock restored, payment voided, excluded from revenue; cannot cancel twice |
| ✅ PASSED | `test_cannot_cancel_after_day_closed_but_admin_can_reopen` | Closed day is locked; admin reopen (audited), correct, re-close |
| ✅ PASSED | `test_locked_closing_row_cannot_be_edited_directly` | Locked closing cannot be edited and financial rows cannot be deleted, even with raw SQL |
| ✅ PASSED | `test_partial_payment_credit_sale_and_collection` | Partial payment / credit sale needs a customer; dues; FIFO collection; ledger balances |
| ✅ PASSED | `test_overpayment_is_rejected` | Payment above the bill total is rejected |
| ✅ PASSED | `test_full_return_with_refund` | Full return: stock back, revenue/COGS reversed, cash refund, cash reconciliation correct |
| ✅ PASSED | `test_partial_return_damaged_item` | Partial return of a damaged unit: not restocked, cost kept as loss, damaged list |
| ✅ PASSED | `test_return_on_credit_sale_reduces_dues_not_cash` | Return on a credit bill reduces dues instead of refunding cash |
| ✅ PASSED | `test_exchange_with_difference_paid` | Exchange for a costlier item: credit applied, difference paid, both bills balanced |
| ✅ PASSED | `test_exchange_for_cheaper_item_refunds_difference` | Exchange for a cheaper item: difference refunded |
| ✅ PASSED | `test_service_job_parts_removed_and_cancel_restores_stock` | Repair: parts deducted/returned, no income until completed, cancel refunds advance & restores stock |
| ✅ PASSED | `test_service_spare_revenue_never_double_counted` | Repair spare-parts revenue counted once; service dues collected |
| ✅ PASSED | `test_excess_advance_refunded_on_delivery` | Advance larger than the bill is refunded at delivery |
| ✅ PASSED | `test_purchase_weighted_average_landed_cost_and_supplier_dues` | Purchase: landed cost (GST + freight), weighted average cost, supplier dues & payment |
| ✅ PASSED | `test_purchase_cancel_reverses_stock` | Purchase cancellation reverses stock and voids the payment |
| ✅ PASSED | `test_gst_inclusive_pricing_and_bill_discount` | GST inclusive prices with bill discount: taxable value and GST exact |
| ✅ PASSED | `test_gst_exclusive_with_round_off` | GST exclusive prices with round-off to the nearest rupee |
| ✅ PASSED | `test_imei_sale_warranty_and_return_to_stock` | IMEI: must be chosen, linked to invoice, warranty expiry, history, return to stock, warranty claim |
| ✅ PASSED | `test_imei_validation` | IMEI validation: count, check digit, already in stock |
| ✅ PASSED | `test_accessories_do_not_need_imei` | Accessories sell without IMEI |
| ✅ PASSED | `test_expense_edit_void_and_future_date` | Expenses: edit, void (not deleted), no future dates |
| ✅ PASSED | `test_closing_requires_earlier_days_and_rolls_business_date` | After Close Day, new entries go to the next business date |
| ✅ PASSED | `test_role_permissions` | Role permissions: sales staff cannot close day, adjust stock or create users; wrong password rejected |
| ✅ PASSED | `test_password_hashing` | Passwords stored as salted PBKDF2 hashes |

## Specification workflow  (`test_workflow.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_spec_section6_charger_example` | Spec §6: 50 chargers → sell 1 → 49 left; sell 2 → 47 left, 3 sold, revenue ₹1,200 |
| ✅ PASSED | `test_section7_quick_sale_two_products` | Spec §7: Product A ₹500 + Product B ₹1,000 = ₹1,500, product-wise revenue kept separately |
| ✅ PASSED | `test_full_workflow` | Spec §23 – the full 20-step workflow (details below) |

## Product categories  (`test_categories.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_added_category_is_listed_inside_its_group` | A new category appears inside its own group (Mobile → Accessories → Spare parts) |
| ✅ PASSED | `test_bad_or_duplicate_names_are_rejected` | Empty, too long, duplicate (any case / spacing) or hidden-duplicate names are refused |
| ✅ PASSED | `test_edit_rename_imei_and_group_rules` | Rename and IMEI default always allowed; the group is locked once products use the category |
| ✅ PASSED | `test_hidden_category_keeps_old_products_but_not_new_ones` | Hidden categories stay on old products but can't be picked for new ones |
| ✅ PASSED | `test_last_visible_category_cannot_be_hidden` | At least one category always stays visible |
| ✅ PASSED | `test_category_changes_are_audited` | Add / edit / hide / show are written to the audit log |
| ✅ PASSED | `test_sales_staff_cannot_change_categories` | Sales staff can't add or hide categories |

## Data reset & undo  (`test_reset.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_preview_matches_reset_and_needs_owner_password` | Preview equals the real reset; a wrong password or missing RESET word stops it |
| ✅ PASSED | `test_sales_reset_for_a_date_range_restores_stock_and_bills` | Date-range sales reset gives stock back and repairs older bills that lost a return |
| ✅ PASSED | `test_reset_all_sales_frees_phones_and_restarts_invoice_numbers` | All-time sales reset frees sold phones and restarts invoice numbers at 00001 |
| ✅ PASSED | `test_purchase_reset_is_blocked_when_its_stock_was_sold` | A purchase whose phones were sold can't be removed alone; together with its sales it can |
| ✅ PASSED | `test_repairs_reset_returns_parts_to_stock` | Repair reset returns the spare parts used to stock |
| ✅ PASSED | `test_stock_reset_sets_everything_to_zero_with_history` | Stock reset sets every product to 0 and keeps the history as adjustments |
| ✅ PASSED | `test_closed_day_figures_are_refreshed_and_closings_can_be_removed` | Closed days keep their counted cash but get updated figures; closings can be removed |
| ✅ PASSED | `test_customers_and_suppliers_only_go_when_nothing_refers_to_them` | Customers / suppliers are removed only when nothing refers to them |
| ✅ PASSED | `test_full_reset_keeps_settings_users_and_categories` | Full reset empties all data but keeps settings, logins, categories and the audit log |
| ✅ PASSED | `test_undo_last_reset_brings_everything_back` | Undo last reset restores the backup taken just before the reset |
| ✅ PASSED | `test_only_the_owner_can_reset` | Managers and staff can't preview or run a reset |
| ✅ PASSED | `test_bad_selections_are_rejected` | Empty choice, reversed or half-filled dates are refused; Products takes its history along |

## Edit bill & owner approval  (`test_edit_approval.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_edit_bill_keeps_the_invoice_number` | An edited bill keeps its number; the old version is kept as …-E1 (cancelled); stock and IMEI follow |
| ✅ PASSED | `test_edit_is_refused_after_return_or_closing` | Bills with returns, or on a closed day, can't be edited |
| ✅ PASSED | `test_staff_cancel_needs_approval_and_it_is_audited` | Staff cancel needs owner approval for that one action; the audit log records who approved |
| ✅ PASSED | `test_staff_discount_limit` | Staff discounts above the limit need approval; the owner has no limit; 0 = no limit |

## Phone link & sync (no cloud)  (`test_phone_sync.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_pairing_is_one_time_and_needs_the_right_code` | A phone links only with the right one-time code; the code works once |
| ✅ PASSED | `test_full_sync_sends_shop_data` | First sync sends products, stock, IMEIs, dues, bills, jobs, expenses; never invents address / GSTIN; idle syncs send nothing |
| ✅ PASSED | `test_phone_bill_is_saved_exactly_once` | A phone bill is saved once even when the phone resends it; old requests are refused |
| ✅ PASSED | `test_last_piece_and_imei_sold_on_both_sides` | Last piece / same IMEI sold on PC and phone: the bill is kept and the owner gets alerts |
| ✅ PASSED | `test_entry_for_a_closed_day_asks_to_update_the_closing` | An entry for a closed day keeps its date; 'Update closing' recalculates that day |
| ✅ PASSED | `test_bill_changed_on_pc_and_phone_becomes_a_conflict` | A bill changed on both sides becomes an alert; 'Use phone version' applies it |
| ✅ PASSED | `test_phone_can_edit_and_cancel_its_own_bills_before_syncing` | Bills made, edited and cancelled on the phone before syncing all land correctly |
| ✅ PASSED | `test_expense_customer_payment_and_repair_job` | Phone expense, dues payment (extra amount alerted) and a full repair job flow |
| ✅ PASSED | `test_removed_phone_cannot_sync` | Remove phone needs the owner's password and stops that phone at once |
| ✅ PASSED | `test_network_service_hello_sync_and_discovery` | The real network service: hello, encrypted sync over HTTP and Wi-Fi discovery |

## Off-PC backups, restore on a new PC, emergency rebuild  (`test_offsite.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_closing_makes_an_encrypted_checked_bundle` | Close Day makes a locked backup (no readable shop data inside); it opens only with the owner password; any changed byte is detected; the closing report is inside |
| ✅ PASSED | `test_recovery_code_opens_backups_when_the_password_is_lost` | The recovery code opens backups (spaces / lower case fine); after a password change new backups use the new password, old ones keep the old one |
| ✅ PASSED | `test_restore_from_file_on_a_new_pc_brings_data_and_logo` | A brand-new PC restores a backup file from a pen drive: data, login, shop logo (moved to the new PC's folder) and the linked phone come back; a wrong password changes nothing |
| ✅ PASSED | `test_second_backup_folder_copy_retention_and_late_warning` | Every backup is copied to the 2nd folder; a missing pen drive is caught up later; "late" warning after 7 days |
| ✅ PASSED | `test_retention_keeps_30_days_and_one_per_month` | 2nd folder keeps the last 30 daily backups plus one per month |
| ✅ PASSED | `test_closing_backup_and_report_reach_the_owner_phone` | After Close Day the owner's phone gets the backup + closing report with the next sync (checked by SHA-256); after the phone confirms, only new ones are sent |
| ✅ PASSED | `test_staff_phone_keeps_backups_only_when_turned_on` | Staff phones get no backups unless the owner turns it on |
| ✅ PASSED | `test_backup_download_over_the_network` | Backup download over the real network service; a replayed request is refused |
| ✅ PASSED | `test_new_pc_restores_the_backup_sent_from_the_phone` | New PC shows a restore code; the phone uploads the backup over HTTP (wrong code / damaged file refused); restore keeps the phone linked and tells it to re-send the entry made after the backup – saved once |
| ✅ PASSED | `test_rebuild_needs_a_waiting_restore_code` | No restore screen open → the phone's copy is refused |
| ✅ PASSED | `test_emergency_rebuild_from_the_phone_copy` | No backup: a new PC is rebuilt from the phone's copy – same logins, product numbers, stock, IMEIs, customer dues, open repair jobs with advances; numbering continues; the phone stays linked; only once, only with the owner password |

## Desktop interface (offscreen GUI tests)  (`test_gui_smoke.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_every_screen_opens` | GUI: all 20 sidebar screens open without errors |
| ✅ PASSED | `test_quick_sale_screen_saves_and_deducts_stock` | GUI: Quick Sale screen saves, deducts stock, dashboard updates automatically |
| ✅ PASSED | `test_billing_screen_multi_item_bill` | GUI: billing screen multi-item bill with bill discount and auto cash payment |
| ✅ PASSED | `test_billing_credit_needs_customer` | GUI: credit bill for walk-in customer is blocked with a clear message |
| ✅ PASSED | `test_repair_job_dialog_adds_part` | GUI: new repair job + add spare part from the job window |
| ✅ PASSED | `test_daily_closing_screen` | GUI: Close Day with cash count and cash kept in drawer |
| ✅ PASSED | `test_settings_screen_saves_shop_details` | GUI: shop details saved from Settings |
| ✅ PASSED | `test_expenses_screen_add_edit_void` | GUI: expense add, edit and void |
| ✅ PASSED | `test_purchase_screen_adds_stock` | GUI: purchase entry adds stock and supplier dues |
| ✅ PASSED | `test_returns_screen_refund` | GUI: return with refund from the Returns screen |
| ✅ PASSED | `test_customer_dues_collection_screen` | GUI: customer dues shown and filtered |
| ✅ PASSED | `test_settings_categories_tab_add_hide_show` | Settings → Categories: add, hide and show again through the real screen |
| ✅ PASSED | `test_product_dialog_plus_button_adds_category` | Add product → + creates a category and the product is saved in it |
| ✅ PASSED | `test_reset_tab_rules_preview_and_run` | Settings → Reset: tick rules, preview and a confirmed reset through the real screen |
| ✅ PASSED | `test_billing_edit_mode_keeps_the_invoice_number` | Sales History → Edit bill opens the bill in Billing; saving keeps the invoice number |
| ✅ PASSED | `test_staff_gets_owner_approval_to_cancel` | Staff cancel works with owner approval; the button hides when approval is switched off |
| ✅ PASSED | `test_phone_link_tab_code_qr_devices_and_alerts` | Settings → Phone link: password lock, one-time code + QR, linked phones and alerts |
| ✅ PASSED | `test_phone_link_tab_turns_backups_on_for_a_staff_phone` | Settings → Phone link: "Keeps backups" column and the on / off button |
| ✅ PASSED | `test_settings_backup_tab_off_pc_backups_and_recovery_code` | Settings → Backup: off-PC backup now, 2nd folder, recovery code (print PDF), "Backup late" pill and the dashboard line |
| ✅ PASSED | `test_new_pc_first_screen_restores_from_phone_or_file` | First screen of a new PC: Restore from phone (QR → backup arrives over the network → password) and Restore from a backup file; Start fresh still works |

## Android phone app  (`android/`, run by GitHub Actions on every push)

| Result | Check | What it proves |
|---|---|---|
| ✅ PASSED | `CalcTest` | 400 bills computed by the PC code give the same paise on the phone (GST incl. / excl., discounts, round off, amount in words) |
| ✅ PASSED | `CryptoTest` | PC password hashes, the pairing key and encrypted envelopes work in both directions (Tamil text too) |
| ✅ PASSED | `RepoTest` | Offline bills, edit, cancel, expense, dues payment and a repair job update stock, dues and today's figures; the sync request holds every entry once |
| ✅ PASSED | `ScreensTest` | All 15 phone screens are drawn with the sample shop |
| ✅ PASSED | `check_phone_ops.py` | The PC code replays the phone's sync request and accepts all 7 entries with exactly the same totals |
