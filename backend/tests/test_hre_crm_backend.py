import os
import io
import pytest
import requests
from typing import Dict, Any

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://b2b-order-hub-11.preview.emergentagent.com").rstrip("/")
# Read frontend env to be source of truth
try:
    with open("/app/frontend/.env") as f:
        for ln in f:
            if ln.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = ln.split("=", 1)[1].strip().strip('"').rstrip("/")
                break
except Exception:
    pass

ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"

# Shared state across tests (function-scoped fixtures share via module-level dict)
STATE: Dict[str, Any] = {}


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def employee_token(admin_client) -> str:
    """Create an employee user directly in DB via mongo (best effort) OR skip if not creatable.
    Since there's no user-create endpoint, we try mongo directly."""
    # Use motor via sync pymongo
    try:
        import pymongo, uuid, bcrypt
        from datetime import datetime, timezone
        mongo_url = "mongodb://localhost:27017"
        with open("/app/backend/.env") as f:
            for ln in f:
                if ln.startswith("MONGO_URL="):
                    mongo_url = ln.split("=", 1)[1].strip().strip('"')
                if ln.startswith("DB_NAME="):
                    db_name = ln.split("=", 1)[1].strip().strip('"')
        cli = pymongo.MongoClient(mongo_url)
        db = cli[db_name]
        email = "TEST_employee@hrexporter.com"
        pw = "EmpPass@123"
        pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        existing = db.users.find_one({"email": email})
        if not existing:
            db.users.insert_one({
                "id": str(uuid.uuid4()), "name": "TEST Employee", "email": email,
                "mobile": "", "password_hash": pw_hash,
                "role": "employee", "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            db.users.update_one({"email": email}, {"$set": {
                "password_hash": pw_hash, "role": "employee", "active": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=20)
        assert r.status_code == 200, r.text
        return r.json()["token"]
    except Exception as e:
        pytest.skip(f"Cannot create employee user: {e}")


# ---------- Auth Tests ----------
class TestAuth:
    def test_login_success(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 20
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "WrongPass!"}, timeout=20)
        assert r.status_code == 401

    def test_me_with_token(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["email"] == ADMIN_EMAIL
        assert u["role"] == "admin"
        assert "password_hash" not in u

    def test_me_without_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert r.status_code == 401


# ---------- Materials ----------
class TestMaterials:
    def test_list_materials_seeded(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/materials")
        assert r.status_code == 200
        items = r.json()
        names = [m["material_name"] for m in items]
        assert "Copper" in names and "Aluminium" in names
        for m in items:
            if m["material_name"] == "Copper":
                STATE["copper_id"] = m["id"]
            if m["material_name"] == "Aluminium":
                STATE["aluminium_id"] = m["id"]

    def test_create_update_delete_material(self, admin_client):
        # CREATE
        payload = {"material_name": "TEST_Brass", "description": "Test brass", "active": True}
        r = admin_client.post(f"{BASE_URL}/api/materials", json=payload)
        assert r.status_code == 200, r.text
        mid = r.json()["id"]
        assert r.json()["material_name"] == "TEST_Brass"
        # UPDATE
        r = admin_client.put(f"{BASE_URL}/api/materials/{mid}",
                             json={"material_name": "TEST_Brass2", "description": "x", "active": False})
        assert r.status_code == 200
        assert r.json()["material_name"] == "TEST_Brass2"
        assert r.json()["active"] is False
        # GET to verify persistence
        r = admin_client.get(f"{BASE_URL}/api/materials")
        names = [m["material_name"] for m in r.json()]
        assert "TEST_Brass2" in names
        # DELETE
        r = admin_client.delete(f"{BASE_URL}/api/materials/{mid}")
        assert r.status_code == 200
        # verify removed
        r = admin_client.get(f"{BASE_URL}/api/materials")
        names = [m["material_name"] for m in r.json()]
        assert "TEST_Brass2" not in names

    def test_employee_cannot_create_material(self, employee_token):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.post(f"{BASE_URL}/api/materials",
                   json={"material_name": "TEST_Forbidden", "description": "x", "active": True})
        assert r.status_code == 403, f"Expected 403 got {r.status_code} {r.text}"


# ---------- Categories ----------
class TestCategories:
    def test_list_categories_seeded(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/categories")
        assert r.status_code == 200
        items = r.json()
        names = [c["category_name"] for c in items]
        assert "Sheet Metal Lug" in names
        assert "Ring Type Lug" in names
        assert "Pin Type Lug" in names
        # verify nested: Ring Type Lug has parent_category_id pointing to Sheet Metal Lug
        sheet = next(c for c in items if c["category_name"] == "Sheet Metal Lug" and c.get("parent_category_id") is None)
        ring = next(c for c in items if c["category_name"] == "Ring Type Lug")
        assert ring["parent_category_id"] == sheet["id"]
        STATE["cu_sheet_id"] = sheet["id"]
        STATE["cu_ring_id"] = ring["id"]

    def test_create_update_delete_category(self, admin_client):
        copper_id = STATE["copper_id"]
        payload = {"category_name": "TEST_Cat", "material_id": copper_id,
                   "parent_category_id": None, "description": "x", "active": True}
        r = admin_client.post(f"{BASE_URL}/api/categories", json=payload)
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        # update
        payload["category_name"] = "TEST_Cat2"
        r = admin_client.put(f"{BASE_URL}/api/categories/{cid}", json=payload)
        assert r.status_code == 200
        assert r.json()["category_name"] == "TEST_Cat2"
        # delete
        r = admin_client.delete(f"{BASE_URL}/api/categories/{cid}")
        assert r.status_code == 200


# ---------- Product Families ----------
class TestProductFamilies:
    def test_list_families_seeded(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-families")
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 3
        names = [f["family_name"] for f in items]
        assert any("Ring Type" in n for n in names)
        # store one for upload tests
        STATE["family_id"] = items[0]["id"]
        # verify technical fields exist
        f0 = items[0]
        for k in ["material_description", "specification_description", "finish_description", "standard_reference"]:
            assert k in f0

    def test_create_update_delete_family(self, admin_client):
        payload = {
            "family_name": "TEST_Family", "short_name": "TF", "material_id": STATE["copper_id"],
            "category_id": STATE["cu_sheet_id"], "subcategory_id": STATE["cu_ring_id"],
            "product_type": "Test", "catalogue_title": "TT", "material_description": "x",
            "specification_description": "x", "finish_description": "x",
            "insulation_colour_coding": "", "standard_reference": "x", "description": "x", "active": True,
        }
        r = admin_client.post(f"{BASE_URL}/api/product-families", json=payload)
        assert r.status_code == 200, r.text
        fid = r.json()["id"]
        assert r.json()["main_product_image"] is None
        # update
        payload["family_name"] = "TEST_Family2"
        r = admin_client.put(f"{BASE_URL}/api/product-families/{fid}", json=payload)
        assert r.status_code == 200
        assert r.json()["family_name"] == "TEST_Family2"
        STATE["test_family_id"] = fid

    def test_upload_main_image(self, admin_client):
        fid = STATE["test_family_id"]
        # tiny PNG bytes
        png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082")
        files = {"file": ("test.png", io.BytesIO(png), "image/png")}
        # send without Content-Type=application/json
        r = requests.post(f"{BASE_URL}/api/product-families/{fid}/upload-image",
                          files=files,
                          headers={"Authorization": admin_client.headers["Authorization"]}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["url"].startswith("/uploads/")

    def test_upload_dimension_drawing(self, admin_client):
        fid = STATE["test_family_id"]
        png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082")
        files = {"file": ("dim.png", io.BytesIO(png), "image/png")}
        r = requests.post(f"{BASE_URL}/api/product-families/{fid}/upload-dimension-drawing",
                          files=files,
                          headers={"Authorization": admin_client.headers["Authorization"]}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["url"].startswith("/uploads/")

    def test_upload_invalid_extension(self, admin_client):
        fid = STATE["test_family_id"]
        files = {"file": ("bad.txt", io.BytesIO(b"hello"), "text/plain")}
        r = requests.post(f"{BASE_URL}/api/product-families/{fid}/upload-image",
                          files=files,
                          headers={"Authorization": admin_client.headers["Authorization"]}, timeout=20)
        assert r.status_code == 400

    def test_delete_test_family(self, admin_client):
        fid = STATE.pop("test_family_id", None)
        if fid:
            r = admin_client.delete(f"{BASE_URL}/api/product-families/{fid}")
            assert r.status_code == 200


# ---------- Product Variants ----------
class TestProductVariants:
    def test_list_variants_seeded(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-variants")
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 9
        codes = [v["product_code"] for v in items]
        assert "RI-7153" in codes and "RII-7057" in codes and "PT-9" in codes
        v = next(x for x in items if x["product_code"] == "RI-7153")
        assert isinstance(v.get("dimensions"), dict) and v["dimensions"].get("A") == "1.6"
        assert isinstance(v.get("final_price"), (int, float)) and v["final_price"] > 0

    def test_filter_by_material(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-variants",
                             params={"material_id": STATE["copper_id"]})
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 9
        assert all(v["material_id"] == STATE["copper_id"] for v in items)

    def test_filter_by_q(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-variants", params={"q": "RI-71"})
        assert r.status_code == 200
        items = r.json()
        assert all("RI-71" in v["product_code"] for v in items)
        assert len(items) >= 1

    def test_create_variant_records_history(self, admin_client):
        # find a family id
        r = admin_client.get(f"{BASE_URL}/api/product-families")
        fam = r.json()[0]
        payload = {
            "product_family_id": fam["id"], "product_code": "TEST-V1",
            "product_name": "TEST Variant", "material_id": STATE["copper_id"],
            "category_id": STATE["cu_sheet_id"], "subcategory_id": STATE["cu_ring_id"],
            "cable_size": "1.5", "hole_size": "3.2", "size": "", "unit": "NOS",
            "hsn_code": "85369090", "gst_percentage": 18.0,
            "base_price": 100.0, "discount_percentage": 10.0,
            "manual_price_override": False, "manual_price": None,
            "minimum_order_quantity": 1, "dimensions": {"A": "1"}, "notes": "", "active": True,
        }
        r = admin_client.post(f"{BASE_URL}/api/product-variants", json=payload)
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["final_price"] == 90.0  # 100 - 10%
        STATE["test_variant_id"] = v["id"]

        # GET to verify persistence
        r = admin_client.get(f"{BASE_URL}/api/product-variants/{v['id']}")
        assert r.status_code == 200
        assert r.json()["final_price"] == 90.0

        # Initial price history recorded
        r = admin_client.get(f"{BASE_URL}/api/product-variants/{v['id']}/price-history")
        assert r.status_code == 200
        hist = r.json()
        assert len(hist) >= 1
        assert hist[0]["new_final_price"] == 90.0

    def test_update_variant_price_history(self, admin_client):
        vid = STATE["test_variant_id"]
        r = admin_client.get(f"{BASE_URL}/api/product-variants/{vid}")
        v = r.json()
        v["base_price"] = 200.0
        v["discount_percentage"] = 25.0
        # Strip server-generated fields
        for k in ["id", "final_price", "created_at", "updated_at"]:
            v.pop(k, None)
        r = admin_client.put(f"{BASE_URL}/api/product-variants/{vid}", json=v)
        assert r.status_code == 200, r.text
        assert r.json()["final_price"] == 150.0  # 200 - 25%

        # history entry added
        r = admin_client.get(f"{BASE_URL}/api/product-variants/{vid}/price-history")
        hist = r.json()
        assert len(hist) >= 2
        assert hist[0]["new_final_price"] == 150.0
        assert hist[0]["old_final_price"] == 90.0

    def test_manual_price_override(self, admin_client):
        vid = STATE["test_variant_id"]
        r = admin_client.get(f"{BASE_URL}/api/product-variants/{vid}")
        v = r.json()
        v["manual_price_override"] = True
        v["manual_price"] = 77.77
        for k in ["id", "final_price", "created_at", "updated_at"]:
            v.pop(k, None)
        r = admin_client.put(f"{BASE_URL}/api/product-variants/{vid}", json=v)
        assert r.status_code == 200, r.text
        assert r.json()["final_price"] == 77.77

    def test_delete_test_variant(self, admin_client):
        vid = STATE.pop("test_variant_id", None)
        if vid:
            r = admin_client.delete(f"{BASE_URL}/api/product-variants/{vid}")
            assert r.status_code == 200


# ---------- Bulk Discount ----------
class TestBulkDiscount:
    def test_bulk_discount_preview_material(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/pricing/bulk-discount/preview",
                              json={"scope": "material", "target_id": STATE["copper_id"]})
        assert r.status_code == 200
        assert r.json()["count"] >= 9

    def test_bulk_discount_preview_invalid_scope(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/pricing/bulk-discount/preview",
                              json={"scope": "bogus", "target_id": "x"})
        assert r.status_code == 400

    def test_bulk_discount_material_apply(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/pricing/bulk-discount/material",
                              json={"discount_percentage": 5.0, "target_id": STATE["copper_id"],
                                    "change_reason": "TEST bulk"})
        assert r.status_code == 200
        assert r.json()["updated_count"] >= 9
        # verify final_price recalculated
        r = admin_client.get(f"{BASE_URL}/api/product-variants",
                             params={"material_id": STATE["copper_id"]})
        items = r.json()
        # all variants without manual override should have discount=5
        non_manual = [v for v in items if not v.get("manual_price_override")]
        assert all(v["discount_percentage"] == 5.0 for v in non_manual)
        # spot check: RI-7153 base=4.50, discount 5% => 4.275 => round 4.27 or 4.28
        ri = next((v for v in items if v["product_code"] == "RI-7153"), None)
        if ri and not ri.get("manual_price_override"):
            # allow rounding tolerance (4.275 -> 4.27 or 4.28 depending on FP)
            assert abs(ri["final_price"] - 4.275) <= 0.011

    def test_bulk_discount_category(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/pricing/bulk-discount/category",
                              json={"discount_percentage": 7.5, "target_id": STATE["cu_ring_id"],
                                    "change_reason": "TEST"})
        assert r.status_code == 200
        assert r.json()["updated_count"] >= 1

    def test_bulk_discount_family(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-families")
        fid = r.json()[0]["id"]
        r = admin_client.post(f"{BASE_URL}/api/pricing/bulk-discount/product-family",
                              json={"discount_percentage": 0.0, "target_id": fid, "change_reason": "TEST reset"})
        assert r.status_code == 200

    def test_employee_cannot_bulk_discount(self, employee_token):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.post(f"{BASE_URL}/api/pricing/bulk-discount/material",
                   json={"discount_percentage": 1.0, "target_id": STATE["copper_id"]})
        assert r.status_code == 403


# ---------- Dashboard & Price History ----------
class TestDashboard:
    def test_dashboard_stats(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard/stats")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["total_families", "total_variants", "active_variants",
                  "total_categories", "material_counts",
                  "recent_families", "recent_price_changes"]:
            assert k in d
        assert d["total_families"] >= 3
        assert d["total_variants"] >= 9
        assert "Copper" in d["material_counts"]
        assert isinstance(d["recent_families"], list)
        assert isinstance(d["recent_price_changes"], list)

    def test_price_history_global(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/price-history", params={"limit": 5})
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert len(items) <= 5
