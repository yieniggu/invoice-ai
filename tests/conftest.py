import os

# Test collection imports the module-level ASGI app; local demo mode is explicit here.
os.environ.setdefault("INVOICEOPS_MODE", "demo")
