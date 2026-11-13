# NJIT Marketplace

A small, responsive storefront using Python's standard library and SQLite. Products,
customer contact details, and order snapshots are stored in the database; the cart
is kept in the shopper's browser. Checkout uses PayPal's hosted payment page, so
card details are never handled by this application.

## Run locally

Requires Python 3.10 or later; no packages need to be installed.

```sh
python3 app.py
```

Visit <http://localhost:8000>. Three sample products are created on first run.
Search and the cart work without payment credentials. To enable checkout, create
a PayPal REST app and sandbox buyer at the [PayPal developer dashboard](https://developer.paypal.com/),
then start the server with its sandbox credentials:

```sh
export PAYPAL_CLIENT_ID='your-client-id'
export PAYPAL_CLIENT_SECRET='your-client-secret'
python3 app.py
```

PayPal determines whether a buyer can pay as a guest with a credit or debit card;
availability depends on the merchant account, buyer location, and PayPal settings.
No payment is marked complete until PayPal confirms the captured USD amount.
After payment, the buyer must return to the same browser tab to complete capture.
If confirmation fails, refresh the return page to retry; do not start a new payment
without checking the existing transaction in PayPal.

## Configuration and deployment

- `DATABASE_PATH`: SQLite file path (default: `marketplace.sqlite3`). Keep this
  file private and back it up; it contains customer data and order records.
- `PAYPAL_ENV`: `sandbox` (default) or `live`. Use the corresponding PayPal
  client ID and secret; never commit credentials.
- `BASE_URL`: public HTTPS origin, for example `https://shop.example.com` (default:
  `http://localhost:8000`). It must match the browser's origin and PayPal return
  URL. HTTP is accepted only for localhost.
- `HOST` and `PORT`: listening address and port (defaults: `127.0.0.1`, `8000`).
  For deployment, put the application behind an HTTPS reverse proxy and set
  `BASE_URL` to the external origin.

Products can be maintained directly in the SQLite `products` table (`name`,
`description`, `price_cents`). Checkout prices are always read from that table,
not trusted from the browser. The sample prices are the complete amount charged;
configure your catalog prices to include any applicable taxes and shipping. This
application does not calculate tax, manage inventory or shipping, send receipts,
or provide an administrative interface; use PayPal's transaction records and
your own fulfillment process.

## Tests

```sh
python3 -m unittest -v
```