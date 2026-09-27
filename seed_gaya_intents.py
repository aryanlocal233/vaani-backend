"""One-off seed: adds the Tier-A intents from the Gaya Ji administration material as DRAFT FAQ
entries (status='draft', reviewed=False) -- keyword triggers only, with an unmistakable
placeholder answer. Nothing here is ever spoken to a real pilgrim until an admin reviews and
approves each one via /admin/faq/<id>, replacing the placeholder with a real, authority-verified
answer. Safe to re-run (upserts).

6 of the original 24 Tier-A intents already exist as approved FAQs (toilet, drinking_water,
parking, help_desk, medical_emergency, lost_and_found) -- not duplicated here. The existing
generic "pind_daan" FAQ also overlaps with the 6 more granular Pind Daan intents below
(location/start/process/time/material/cost) -- both are left in place; consolidating them is an
admin decision once the granular ones are reviewed and approved, not made automatically here.
"""
import asyncio

from dotenv import load_dotenv

import db

load_dotenv()

PACK_ID = "gaya_ji"
PLACEHOLDER = {
    "hi": "[ड्राफ्ट — इस्तेमाल से पहले गया जी प्राधिकरण द्वारा सत्यापित उत्तर आवश्यक है।]",
    "en": "[DRAFT -- needs a Gaya Ji authority-verified answer before use.]",
}

INTENTS: list[dict] = [
    {
        "id": "vishnupad_direction", "category": "location",
        "keywords": {
            "hi": ["Vishnupad कहाँ है", "विष्णुपद कहाँ है", "विष्णुपद मंदिर कहाँ"],
            "en": ["where is vishnupad", "vishnupad temple location"],
        },
    },
    {
        "id": "vishnupad_route", "category": "location",
        "keywords": {
            "hi": ["Vishnupad कैसे जाएँ", "विष्णुपद कैसे जाएं", "विष्णुपद जाने का रास्ता"],
            "en": ["how to reach vishnupad", "route to vishnupad", "way to vishnupad"],
        },
    },
    {
        "id": "falgu_direction", "category": "location",
        "keywords": {
            "hi": ["Falgu नदी कहाँ", "फल्गु घाट कहाँ", "फल्गु नदी कैसे जाएं"],
            "en": ["where is falgu river", "falgu ghat location"],
        },
    },
    {
        "id": "akshayvat_direction", "category": "location",
        "keywords": {
            "hi": ["Akshay Vat कहाँ है", "अक्षय वट कहाँ है", "अक्षयवट कैसे जाएं"],
            "en": ["where is akshay vat", "akshayvat location"],
        },
    },
    {
        "id": "pretshila_direction", "category": "location",
        "keywords": {
            "hi": ["Pretshila कैसे जाएं", "प्रेतशिला कहाँ है", "प्रेतशिला जाने का रास्ता"],
            "en": ["where is pretshila", "how to reach pretshila"],
        },
    },
    {
        "id": "ramshila_direction", "category": "location",
        "keywords": {
            "hi": ["Ramshila कहाँ है", "रामशिला कहाँ है", "रामशिला कैसे जाएं"],
            "en": ["where is ramshila", "how to reach ramshila"],
        },
    },
    {
        "id": "pinddaan_location", "category": "location",
        "keywords": {
            "hi": ["Pind Daan कहाँ होता है", "पिंड दान कहाँ होता है", "कौन सी वेदी पर पिंड दान"],
            "en": ["where does pind daan happen", "pind daan location", "which vedi for pind daan"],
        },
    },
    {
        "id": "pinddaan_start", "category": "static",
        "keywords": {
            "hi": ["Pind Daan कहाँ से शुरू करें", "पिंड दान कहाँ से शुरू", "सबसे पहले कौन सी वेदी"],
            "en": ["where to start pind daan", "first vedi for pind daan"],
        },
    },
    {
        "id": "pandit_location", "category": "location",
        "keywords": {
            "hi": ["Panda Ji कहाँ मिलेंगे", "पंडित जी कहाँ मिलेंगे", "हमारे पंडा जी कैसे खोजें"],
            "en": ["where to find panda ji", "where to find pandit", "find our family panda"],
        },
    },
    {
        "id": "pinddaan_process", "category": "static",
        "keywords": {
            "hi": ["Pind Daan कैसे करना है", "पिंड दान की विधि", "पिंड दान कैसे होता है"],
            "en": ["how to do pind daan", "pind daan process", "pind daan procedure"],
        },
    },
    {
        "id": "pinddaan_time", "category": "time",
        "keywords": {
            "hi": ["Pind Daan में कितना समय लगेगा", "पिंड दान में कितने दिन लगते हैं", "पिंड दान का सही समय"],
            "en": ["how long does pind daan take", "pind daan duration", "best time for pind daan"],
        },
    },
    {
        "id": "pinddaan_material", "category": "static",
        "keywords": {
            "hi": ["Pind Daan के लिए क्या सामान चाहिए", "पिंड दान का सामान", "सामान कहाँ मिलेगा"],
            "en": ["what materials needed for pind daan", "pind daan items required"],
        },
    },
    {
        "id": "pinddaan_cost", "category": "static",
        "keywords": {
            "hi": ["Pind Daan का कितना खर्च है", "पिंड दान की कीमत", "पिंड दान में कितना पैसा लगेगा"],
            "en": ["cost of pind daan", "how much does pind daan cost", "pind daan price"],
        },
    },
    {
        "id": "food_nearest", "category": "location",
        "keywords": {
            "hi": ["खाना कहाँ मिलेगा", "भोजन कहाँ मिलेगा", "प्रसाद कहाँ मिलेगा"],
            "en": ["where can i get food", "nearest food", "where is prasad"],
        },
    },
    {
        "id": "bus_stand", "category": "location",
        "keywords": {
            "hi": ["Bus stand कहाँ है", "बस स्टैंड कहाँ है", "बस कहाँ मिलेगी"],
            "en": ["where is the bus stand", "where can i find a bus"],
        },
    },
    {
        "id": "railway_station", "category": "location",
        "keywords": {
            "hi": ["Railway station कैसे जाएं", "स्टेशन कैसे जाएं", "गया जंक्शन कैसे जाएं"],
            "en": ["how to reach railway station", "how to reach gaya junction"],
        },
    },
    {
        "id": "police_help", "category": "emergency",
        "keywords": {
            "hi": ["Police camp कहाँ है", "पुलिस कहाँ है", "पुलिस को बुलाइए", "शिकायत कहाँ करें"],
            "en": ["where is the police camp", "call the police", "where to file a complaint"],
        },
    },
    {
        "id": "accommodation", "category": "location",
        "keywords": {
            "hi": ["रहने की व्यवस्था कहाँ है", "Tent City कहाँ है", "धर्मशाला कहाँ है", "ठहरने की जगह"],
            "en": ["where can i stay", "accommodation location", "tent city location", "dharamshala"],
        },
    },
]


async def main() -> None:
    await db.init_pool()
    for intent in INTENTS:
        await db.create_faq(PACK_ID, intent["id"], intent["category"], "claude-draft-seed")
        # create_faq sets status='approved' by default -- immediately downgrade to draft so
        # nothing here is ever spoken to a real pilgrim before review.
        await db.upsert_faq(PACK_ID, intent["id"], intent["category"], "draft", "claude-draft-seed")
        for lang, keywords in intent["keywords"].items():
            await db.replace_faq_keywords(PACK_ID, intent["id"], lang, keywords)
        for lang, text in PLACEHOLDER.items():
            await db.upsert_faq_answer(PACK_ID, intent["id"], lang, text, "claude-draft-seed", reviewed=False)
        print(f"Seeded draft intent: {intent['id']}")
    print(f"\nDone. {len(INTENTS)} draft intents seeded into pack '{PACK_ID}'.")
    print("None are live -- review and approve each one at /admin/faq/<id> before they answer real pilgrims.")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
