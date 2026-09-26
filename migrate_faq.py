"""One-off migration: seeds the 'gaya_ji' knowledge pack in Postgres from the
data that used to be hardcoded as FAQ_ENTRIES in sarvam_server.py. Run once
against a fresh schema; safe to re-run (upserts)."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

FAQ_ENTRIES = [
    {
        "id": "toilet",
        "category": "location",
        "keywords": {
            "hi": ["शौचालय", "टॉयलेट", "टॉयलट", "बाथरूम"],
            "en": ["toilet", "washroom", "restroom", "bathroom"],
        },
        "answer": {
            "hi": "शौचालय यहाँ से सीधे जाकर बाईं ओर है।",
            "en": "The toilet is straight ahead, then to your left.",
        },
    },
    {
        "id": "drinking_water",
        "category": "location",
        "keywords": {
            "hi": ["पानी", "पीने का पानी", "पेयजल"],
            "en": ["drinking water", "water"],
        },
        "answer": {
            "hi": "पीने के पानी का स्टॉल हर काउंटर के पास लगा है, आगे बढ़ते ही दिख जाएगा।",
            "en": "Drinking water stalls are set up near every counter, you'll see one just ahead.",
        },
    },
    {
        "id": "pind_daan",
        "category": "static",
        "keywords": {
            "hi": ["पिंड दान", "पिंडदान", "श्राद्ध"],
            "en": ["pind daan", "pinddaan", "shraddha"],
        },
        "answer": {
            "hi": "पिंड दान के लिए निर्धारित घाट पर जाएं, वहां पंडित जी प्रक्रिया में सहायता करेंगे।",
            "en": "Please go to the designated ghat for Pind Daan -- a pandit there will guide you through the procedure.",
        },
    },
    {
        "id": "lost_and_found",
        "category": "location",
        "keywords": {
            "hi": ["खो गया", "खो गई", "गुम हो", "लॉस्ट एंड फाउंड"],
            "en": ["lost and found", "lost my", "missing person", "i lost"],
        },
        "answer": {
            "hi": "खोया-पाया केंद्र मुख्य द्वार के पास है, कृपया वहां जाकर सूचना दर्ज कराएं।",
            "en": "The Lost & Found center is near the main gate -- please go there and file a report.",
        },
    },
    {
        "id": "medical_emergency",
        "category": "emergency",
        "keywords": {
            "hi": ["डॉक्टर", "अस्पताल", "मेडिकल", "इमरजेंसी", "तबीयत खराब"],
            "en": ["doctor", "hospital", "medical", "emergency", "not feeling well", "unwell"],
        },
        "answer": {
            "hi": "निकटतम मेडिकल कैंप की ओर तुरंत जाएं, हर काउंटर के पास एक साइनबोर्ड लगा है। गंभीर स्थिति में यहां के कर्मचारी से तुरंत सहायता मांगें।",
            "en": "Please go to the nearest medical camp right away -- there's a signboard near every counter. For a serious emergency, ask any staff member here immediately.",
        },
    },
    {
        "id": "timings",
        "category": "time",
        "keywords": {
            "hi": ["समय क्या है", "कब खुलता", "कब बंद", "टाइमिंग"],
            "en": ["timing", "what time", "opening time", "closing time"],
        },
        "answer": {
            "hi": "यह स्थान सुबह पांच बजे से रात नौ बजे तक खुला रहता है।",
            "en": "This site is open from 5 AM to 9 PM.",
        },
    },
    {
        "id": "parking",
        "category": "location",
        "keywords": {
            "hi": ["पार्किंग", "गाड़ी कहां खड़ी"],
            "en": ["parking", "where can i park", "car park"],
        },
        "answer": {
            "hi": "पार्किंग की सुविधा मुख्य द्वार से पहले, दाईं ओर उपलब्ध है।",
            "en": "Parking is available on the right, just before the main gate.",
        },
    },
    {
        "id": "help_desk",
        "category": "static",
        "keywords": {
            "hi": ["हेल्प डेस्क", "सहायता केंद्र", "मदद चाहिए"],
            "en": ["help desk", "information desk", "i need help"],
        },
        "answer": {
            "hi": "आप अभी हेल्प डेस्क काउंटर पर ही हैं। कृपया बताएं आपको किस चीज़ में सहायता चाहिए।",
            "en": "You're already at a help desk counter. Please tell us what you need help with.",
        },
    },
]

PACK_ID = "gaya_ji"
PACK_NAME = "Gaya Ji Pitru Paksha Mela"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute(
            "INSERT INTO knowledge_packs (id, name) VALUES ($1, $2) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            PACK_ID, PACK_NAME,
        )
        for entry in FAQ_ENTRIES:
            faq_id = entry["id"]
            await conn.execute(
                "INSERT INTO faq_knowledge (pack_id, faq_id, category, status, approved_by) "
                "VALUES ($1, $2, $3, 'approved', 'migration') "
                "ON CONFLICT (pack_id, faq_id) DO UPDATE SET category = EXCLUDED.category",
                PACK_ID, faq_id, entry["category"],
            )
            for lang, keywords in entry["keywords"].items():
                await conn.execute(
                    "DELETE FROM faq_keywords WHERE pack_id = $1 AND faq_id = $2 AND language = $3",
                    PACK_ID, faq_id, lang,
                )
                for kw in keywords:
                    await conn.execute(
                        "INSERT INTO faq_keywords (pack_id, faq_id, language, keyword) VALUES ($1, $2, $3, $4)",
                        PACK_ID, faq_id, lang, kw,
                    )
            for lang, answer_text in entry["answer"].items():
                await conn.execute(
                    "INSERT INTO faq_answers (pack_id, faq_id, language, answer_text, reviewed) "
                    "VALUES ($1, $2, $3, $4, TRUE) "
                    "ON CONFLICT (pack_id, faq_id, language) DO UPDATE SET answer_text = EXCLUDED.answer_text",
                    PACK_ID, faq_id, lang, answer_text,
                )
            print(f"Migrated FAQ '{faq_id}'")
        print(f"\nDone. Pack '{PACK_ID}' has {len(FAQ_ENTRIES)} FAQ entries.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
