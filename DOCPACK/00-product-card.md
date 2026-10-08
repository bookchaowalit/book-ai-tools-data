---
title: Book AI Tools Data — Product Card
doc_id: CARD-book-ai-tools-data
doc_type: product-card
verdict: showcase
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
---

# Book AI Tools Data — Product Card

## 1. สรุปคุณค่าในหนึ่งประโยค

เก็บรายการเครื่องมือ AI จาก directory สาธารณะเข้า data lake แบบ lake-first (landing → Bronze → DuckDB API) พร้อม lineage — เป็นหลักฐานงาน data engineering ที่ดี มากกว่าเป็นผลิตภัณฑ์ที่ผู้ใช้ภายนอกต้องการ

## 2. ปัญหาและผู้ใช้เป้าหมาย

- ปัญหา: ติดตามเครื่องมือ AI ใหม่ ๆ และราคาจากหลาย directory
- ผู้ใช้: operator/agent ภายใน Solo Empire ที่ต้องการ feed เครื่องมือ AI; ผู้ใช้ภายนอก **[ต้องยืนยัน]**

## 3. สิ่งที่ทำได้จริงตอนนี้

- `book_ai_tools.ingest`: แหล่ง `producthunt`, `taaft`, `search`, `--fixture`; เขียน HTML exact bytes ไป landing และ Bronze `ai_tools`/`ai_tools_history`
- `book_ai_tools.api` (port 8107): `GET /v1/metadata`, `/v1/records`, `/v1/history`; `POST /v1/refresh` ปิด (403) โดยค่าเริ่มต้น
- provider policy: Firecrawl/Brave/paid search ถูก block ก่อนเรียก network
- Futurepedia collector + parser ทดสอบ offline; Iceberg REST read pilot มีหลักฐาน parity 2026-08-08 (2 tools + 2 history)

## 4. หลักฐานสถานะ

| รายการ | ค่า (ตรวจจาก repository) |
|---|---|
| Stack | Python |
| Commits | 18 (แรก 2026-08-11 → ล่าสุด 2026-10-01) |
| Source | 23 ไฟล์ / 3,737 บรรทัด |
| Tests | 12 ไฟล์ |
| CI | 1 workflow |
| Deploy config | ไม่มี |
| Live URL | ไม่มี |
| README | 159 บรรทัด (`book-ai-tools-data`) |
| โครงสร้างซ้ำ (template family) | 3 repo ที่มี structure signature เดียวกัน |

อยู่ใน baseline ของ `repository-catalog/policies/BOOK-DEV-DATA-PRODUCTS-CONTRACT.md`

## 5. คุณค่าและจุดต่าง

- จุดต่างไม่ได้อยู่ที่ข้อมูล (Futurepedia/TAAFT เองทำ directory ที่ใหญ่กว่า) แต่อยู่ที่ contract, lineage, free-only policy และ Iceberg parity — ใช้เป็นตัวอย่างอ้างอิงของ data product ใน portfolio
- มุมรายได้: newsletter เครื่องมือ AI ใหม่ **[สมมติฐาน]** — ตลาดแข่งขันสูง

## 6. ช่องว่างสำคัญ

- ข้อมูลจริงใน lake มีน้อย (หลักฐาน parity ใช้ 2 records) และสถานะ `stale` เป็นที่คาดไว้
- ไม่มี consumer ที่ใช้ feed นี้ตัดสินใจจริง **[ต้องยืนยัน]**
- `book-opportunity-scraping/scrape_ai_tools.py` ทำงานซ้ำซ้อนกับ repo นี้

## 7. คำตัดสิน

**`showcase`** — เป็นหลักฐาน lake-first ที่ดีและควรรักษาไว้ แต่ยังไม่ใช่ธุรกิจ จะเปลี่ยนเป็น `invest` เมื่อมี consumer ที่ใช้ feed นี้สม่ำเสมอ (เช่น digest เครื่องมือ AI รายสัปดาห์ที่มีผู้อ่าน)

## 8. ขั้นต่อไป

1. ให้ `book-opportunity-scraping` เลิกเก็บ AI tools แล้วชี้มาที่ repo นี้
2. รักษา README/หลักฐาน parity ให้ตรงสถานะจริง
3. ตัดสินว่ามี consumer ใดจะอ่าน `/v1/records`; ถ้าไม่มี ให้คงเป็น showcase

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner (Book Dev) | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `README.md`
- `src/book_ai_tools/api.py`
- `src/book_ai_tools/ingest.py`
- `.github/workflows/ci.yml`
- `(solo-empire) repository-catalog/policies/BOOK-DEV-DATA-PRODUCTS-CONTRACT.md`
