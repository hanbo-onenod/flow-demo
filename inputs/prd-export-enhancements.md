# PRD: Export enhancements (fictional, demo only)

Author: Product team · Version 0.3 · 2026-11-12

## 1. Background

Customers export order reports every week to reconcile with their own books. Today the export always contains every order since the beginning of the year, and only as CSV.

## 2. Filter by date range

Users can pass a start date and an end date; only orders whose `order_date` falls within the range (inclusive) are exported. If no range is given, behaviour stays as today. Invalid dates must produce a clear error message, not an empty file.

Success: exporting one month out of ten months of data returns exactly that month's orders.

## 3. Export as Excel

Users can choose `.xlsx` as the output format. Column order and headers match the CSV. Amounts are numbers, not text, so Excel can sum them.

Success: the file opens in Excel and LibreOffice without warnings, and the amount column sums correctly.

## 4. Out of scope

PDF export, scheduled exports, email delivery.
