# Test results – SDJ Mobile Care

**Result: 48 passed in 55.38s** · run on 19 Nov 2026 · Python 3.13.18 · PySide6 6.12.0 · SQLAlchemy 2.1.9 · ReportLab 5.0.1 · Matplotlib 3.11.2 · OpenPyXL 3.1.5

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

## Specification workflow  (`test_workflow.py`)

| Result | Test | What it proves |
|---|---|---|
| ✅ PASSED | `test_spec_section6_charger_example` | Spec §6: 50 chargers → sell 1 → 49 left; sell 2 → 47 left, 3 sold, revenue ₹1,200 |
| ✅ PASSED | `test_section7_quick_sale_two_products` | Spec §7: Product A ₹500 + Product B ₹1,000 = ₹1,500, product-wise revenue kept separately |
| ✅ PASSED | `test_full_workflow` | Spec §23 – the full 20-step workflow (details below) |

## The 20-step workflow (spec section 23) – `test_full_workflow`

| # | Step | Verified result |
|---|---|---|
| 1–3 | Add 50 chargers (₹250/₹400), 20 earphones (₹150/₹300), 10 displays (₹800/₹1,000) | opening stock recorded |
| 4 | Sell 2 chargers (quick sale, cash) | bill ₹800 |
| 5 | Sell 3 earphones (billing, UPI) | bill ₹900, fully paid |
| 6 | Sell 1 display (cash) | bill ₹1,000 |
| 7 | Verify stock deduction | chargers 48 · earphones 17 · displays 9 |
| 8 | Verify product-wise sales | 2 × ₹800 · 3 × ₹900 · 1 × ₹1,000 |
| 9 | Verify profit | COGS ₹1,750 · gross profit ₹950 |
| 10–12 | Repair job, one display used (charged ₹1,000), labour ₹500 | job total ₹1,500 |
| 13 | Verify spare stock deduction | displays 8 |
| 14 | Verify service income | labour ₹500 + parts ₹1,000 = ₹1,500; parts profit ₹200 |
| 15 | Daily expenses | ₹200 food (cash) + ₹300 transport (UPI) |
| 16 | Verify net profit | net sales ₹3,700 · COGS ₹2,550 · gross profit ₹1,150 · **net operating profit ₹1,150** · total revenue ₹4,200 (no double counting) |
| 17 | Close the business day | expected cash ₹3,100 = ₹3,300 cash received − ₹200 cash expense; difference ₹0; record locked; backup taken; next entries go to the next day; stock carried forward |
| 18 | Daily PDF | valid PDF generated |
| 19 | Monthly report | revenue ₹4,200 · gross profit ₹1,150 · net profit ₹1,150 · service ₹1,500 · closing stock ₹20,950 (48×₹250 + 17×₹150 + 8×₹800); PDF, Excel, CSV exported |
| 20 | Verify all calculations | product-wise sales sum to net sales; every product: opening + purchased − sold − service + returned + adjusted = closing; A4, 80 mm and 58 mm invoices generated |

## Built application self-test

The PyInstaller build was produced from `sdj_mobile_care.spec` and its built-in self-test (`SDJMobileCare --self-test`) passed:
database creation, sale with stock deduction, repair job, invoice / receipt / service-bill PDFs, daily closing with PDF and backup
(net operating profit ₹1,000.00 as expected), Excel export, Qt PDF module, and all 20 screens opening.
The GitHub workflow and `build_windows.bat` run the same self-test on the Windows build.

## Sample database consistency

`sample_data/sdj_sample.db` (60 days, ~395 bills, ~143 repair jobs, 60 closings): for every product the stock quantity equals
the sum of its stock-movement history (0 mismatches).
