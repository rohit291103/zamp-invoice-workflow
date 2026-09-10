"""The sample invoices, and the story behind each one.

Shared by the web app and the CLI runner so a run is described the same way
whichever entry point started it.

Each entry carries more than a label. Someone opening this for the first time
has no idea what "happy path" means or why a scanned PDF is interesting, so
every scenario states the business situation in plain language, what a naive
system would get wrong, and what to watch for while it runs.
"""
from __future__ import annotations

CATALOGUE: dict[str, dict[str, object]] = {
    "01_happy_path.pdf": {
        "title": "The straightforward one",
        "kind": "happy",
        "group_note": "What most invoices look like on a good day.",
        "blurb": "Clean text PDF, approved vendor, exact PO match.",
        "story": (
            "An office supplies company sends its monthly invoice for $3,150. "
            "It is a normal PDF, the vendor is on our approved list, and the amount "
            "matches the purchase order exactly."
        ),
        "hard": (
            "Nothing. This is the baseline — roughly this shape is what a large "
            "share of a real accounts-payable queue looks like, and none of it "
            "should need a human."
        ),
        "watch": "All seven stages pass with no warnings. Nobody touches it.",
        "why": "Every check passes, so it is released for payment automatically.",
        "expect": "AUTO_APPROVE",
    },
    "02_edge_scanned_freight.pdf": {
        "title": "A photocopy, not a file",
        "kind": "edge",
        "edge_no": 1,
        "blurb": "No text layer at all — the page is an image.",
        "story": (
            "A Dutch freight company invoices €12,400. Somebody scanned a paper "
            "copy on a flatbed and emailed the result. The page is slightly "
            "crooked, a bit blurry, and contains zero selectable text."
        ),
        "hard": (
            "You cannot read this file the normal way — there is nothing to read. "
            "Every number has to be interpreted from pixels. The real question "
            "isn't whether a model can do that, it's how much a correct-looking "
            "reading of a blurry number should be trusted with money."
        ),
        "watch": (
            "Stage 1 detects zero characters and switches to the vision path. "
            "Confidence gets capped at 80%, under the 85% bar for auto-approval."
        ),
        "why": (
            "It reads the invoice correctly, and still refuses to pay it unattended. "
            "How the data was read decides how far it can be trusted."
        ),
        "expect": "NEEDS_REVIEW",
    },
    "03a_split_release_1.pdf": {
        "title": "Part 1 of 3 against one order",
        "kind": "edge",
        "edge_no": 2,
        "blurb": "First delivery against a $27,000 order.",
        "story": (
            "A packaging supplier has a $27,000 purchase order to deliver boxes in "
            "three batches. This is the first batch, invoiced at $12,000."
        ),
        "hard": (
            "Nothing yet — but this is the setup. Run all three in order to see "
            "why the third one matters."
        ),
        "watch": "$12,000 of $27,000 used. Plenty of room left on the order.",
        "why": "Partial billing is allowed on this order and there is budget left.",
        "expect": "AUTO_APPROVE",
    },
    "03b_split_release_2.pdf": {
        "title": "Part 2 of 3 against one order",
        "kind": "edge",
        "edge_no": 2,
        "blurb": "Second delivery. Running total now $22,500.",
        "story": "The second batch of boxes arrives, invoiced at $10,500.",
        "hard": (
            "Still fine — but notice the process is now tracking a running total, "
            "not just looking at this one invoice."
        ),
        "watch": "The match stage reports $22,500 of $27,000 charged so far.",
        "why": "Cumulative total is still under the order value.",
        "expect": "AUTO_APPROVE",
    },
    "03c_split_release_3.pdf": {
        "title": "Part 3 of 3 — the overrun",
        "kind": "edge",
        "edge_no": 2,
        "blurb": "Looks fine alone. Takes the order to $28,700.",
        "story": (
            "The third and final batch, invoiced at $6,200. On its own this is a "
            "perfectly ordinary invoice against a $27,000 order."
        ),
        "hard": (
            "No single invoice here is wrong. $6,200 against a $27,000 order looks "
            "completely reasonable. Only the three together overrun the order — by "
            "$1,700. A system that examines one invoice at a time pays this."
        ),
        "watch": (
            "Run 1 and 2 first, or there is no history for this to trip over. "
            "The match stage lists the two earlier invoices it found."
        ),
        "why": (
            "The cumulative total exceeds what we agreed to buy, so it stops and "
            "asks procurement rather than quietly overpaying."
        ),
        "expect": "HOLD",
    },
    "04_edge_duplicate_resubmit.pdf": {
        "title": "The same bill, sent twice",
        "kind": "edge",
        "edge_no": 3,
        "blurb": "Same invoice number as the first one, new layout.",
        "story": (
            "The office supplies company chases payment and re-sends invoice "
            "INV-KS-8841. Their system regenerated the PDF, so it now uses a "
            "completely different template — different fonts, layout, and file."
        ),
        "hard": (
            "Comparing files does not work: not one byte matches the original. "
            "This is also the common case in real life, because vendor systems "
            "regenerate the document whenever they resend it."
        ),
        "watch": "Run the straightforward one first so there is something to match against.",
        "why": (
            "Identity is the vendor plus the invoice number read from the page, "
            "never the file itself. Paying it again would be a double payment."
        ),
        "expect": "REJECT",
    },
    "05_edge_no_po_no_number.pdf": {
        "title": "Missing its own identifiers",
        "kind": "edge",
        "edge_no": 4,
        "blurb": "No order reference, and the invoice number was left blank.",
        "story": (
            "A steel supplier invoices $48,600. The form has an 'Invoice No.' field "
            "printed on it, but nobody filled it in, and there is no purchase order "
            "reference — just 'as per contract'."
        ),
        "hard": (
            "Two gaps that look similar and are not. The order can be worked out: "
            "this supplier has exactly one open order matching that amount. The "
            "invoice number cannot be worked out — and it is the thing we use to "
            "make sure a bill is never paid twice."
        ),
        "watch": (
            "The match stage says 'inferred' rather than 'found'. The process still "
            "refuses to release payment, and says exactly what would unblock it."
        ),
        "why": (
            "Everything reconciles perfectly and it still holds. Knowing which gap "
            "you may fill in and which you must not is the whole judgement."
        ),
        "expect": "HOLD",
    },
    "09_edge_same_po_new_number.pdf": {
        "title": "One order, billed twice",
        "kind": "edge",
        "edge_no": 5,
        "blurb": "A genuinely new invoice number against an already-settled order.",
        "story": (
            "A week after being paid, the office supplies company bills the same "
            "$3,150 purchase order again — this time under a brand new invoice "
            "number, INV-KS-8907."
        ),
        "hard": (
            "It passes every check individually. It is not a duplicate — the "
            "invoice number is real and new. The vendor is approved. The order is "
            "open. The amount matches the order exactly, so there is no variance. "
            "Only asking 'has this order already been settled?' catches it."
        ),
        "watch": (
            "Run the straightforward one first. Two separate rules flag this from "
            "different angles — one blocking, one advisory."
        ),
        "why": (
            "This one was not designed. It surfaced during the build, when the "
            "purchase-order view showed an order billed to twice its value with "
            "nothing flagged."
        ),
        "expect": "HOLD",
    },
    "06_blocked_vendor.pdf": {
        "title": "A supplier we cannot pay",
        "kind": "rule",
        "group_note": "Ordinary controls, shown one at a time.",
        "blurb": "Vendor failed a sanctions re-screen.",
        "story": (
            "An IT services firm invoices $41,200 against a valid order. The "
            "paperwork is perfect. The supplier failed a sanctions re-screen "
            "three weeks ago and is blocked in our vendor master."
        ),
        "hard": "The invoice is flawless. The problem is not on the page at all.",
        "watch": "The vendor check fails at stage 4, before anything else matters.",
        "why": "A blocked supplier cannot be paid, whatever the invoice says.",
        "expect": "REJECT",
    },
    "07_tolerance_minor_overage.pdf": {
        "title": "A few dollars over",
        "kind": "rule",
        "blurb": "$120 over a $6,400 order — inside the allowed band.",
        "story": (
            "A steel supplier invoices $6,520 against a $6,400 order. The extra "
            "$120 is the kind of difference freight or rounding produces."
        ),
        "hard": (
            "Holding every invoice that is a few dollars out would stall the whole "
            "queue. Approving anything at all would defeat the point. So there is "
            "a band: 2% of the order, or $25, whichever is larger."
        ),
        "watch": "The variance is reported and accepted, with the numbers shown.",
        "why": "$120 is inside the $128 band for this order, so it goes through.",
        "expect": "AUTO_APPROVE",
    },
    "08_vendor_on_hold.pdf": {
        "title": "A supplier with expired paperwork",
        "kind": "rule",
        "blurb": "Consultancy with a lapsed tax certificate.",
        "story": (
            "A consultancy invoices its $15,000 monthly retainer. Everything "
            "matches, but their W-9 tax certificate expired in June."
        ),
        "hard": (
            "This is not fraud and not an error — it is an admin gap. It should "
            "slow the payment down, not stop it dead."
        ),
        "watch": "A warning rather than a block, and the reason is named.",
        "why": "Payable, but somebody signs off and chases the paperwork.",
        "expect": "NEEDS_REVIEW",
    },
}

# Ordering for the demo sequence and the picker.
DEMO_SEQUENCE = list(CATALOGUE)
