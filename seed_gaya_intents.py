"""One-off seed: adds Gaya Ji intents as DRAFT FAQ entries (status='draft') -- keyword triggers
only. Nothing here is ever spoken to a real pilgrim until an admin approves it via
/admin/faq/<id>, replacing/confirming the answer with a real, authority-verified one. Safe to
re-run (upserts).

Two kinds of draft content, distinguished by `reviewed` on the answer row:
  - Tier-A intents (Gaya Ji-specific: locations, timings, ritual procedure/cost) have NO safe
    generic answer AI can supply -- these get the unmistakable PLACEHOLDER text and
    reviewed=False. An admin must write the real answer from scratch. Don't let AI invent
    religious instructions; only the mela authority/Gayawal experts can approve these.
  - Tier-B intents (facilities/safety/connectivity questions that are the same at any large
    Indian gathering, not specific to Gaya Ji) get a `draft_answer` -- generic, verifiable public
    information (e.g. national emergency numbers) -- and reviewed=True, meaning: an admin should
    still read it and confirm it fits this mela before flipping status to 'approved', but it is
    NOT a blank slate the way the Tier-A placeholders are.
  status stays 'draft' for every intent in this file regardless of `reviewed` -- nothing here
  goes live to pilgrims without an explicit admin status flip.

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

    # --- Tier-B: generic facilities/safety/connectivity intents. Same at any large Indian
    # gathering, not Gaya-specific -- draft_answer given (reviewed=True), but status still
    # 'draft' until an admin confirms it fits this mela and flips it live.
    {
        "id": "emergency_numbers", "category": "emergency",
        "keywords": {
            "hi": ["इमरजेंसी नंबर", "आपातकालीन नंबर", "एम्बुलेंस नंबर", "पुलिस नंबर क्या है"],
            "en": ["emergency number", "ambulance number", "what is the police number", "helpline number"],
        },
        "draft_answer": {
            "hi": "आपातकाल में: पुलिस 100, एम्बुलेंस 108, फायर 101, महिला हेल्पलाइन 1091, बाल हेल्पलाइन 1098, एकीकृत आपातकालीन नंबर 112। नजदीकी सहायता केंद्र पर भी सूचित करें।",
            "en": "In an emergency: Police 100, Ambulance 108, Fire 101, Women's helpline 1091, Child helpline 1098, unified emergency number 112. Also alert the nearest help desk.",
        },
    },
    {
        "id": "mobile_charging", "category": "location",
        "keywords": {
            "hi": ["मोबाइल चार्जिंग कहाँ है", "फोन चार्ज करने की जगह", "चार्जिंग पॉइंट"],
            "en": ["where can i charge my phone", "mobile charging point", "charging station"],
        },
    },
    {
        "id": "wifi_availability", "category": "static",
        "keywords": {
            "hi": ["वाईफाई है क्या", "इंटरनेट कैसे मिलेगा", "फ्री वाईफाई कहाँ है"],
            "en": ["is there wifi", "free wifi available", "internet access"],
        },
    },
    {
        "id": "network_congestion", "category": "static",
        "keywords": {
            "hi": ["नेटवर्क नहीं आ रहा", "फोन में नेटवर्क क्यों नहीं है", "कॉल क्यों नहीं लग रही"],
            "en": ["no network signal", "phone network not working", "call not connecting"],
        },
        "draft_answer": {
            "hi": "बड़ी भीड़ के कारण मोबाइल नेटवर्क कभी-कभी धीमा हो सकता है। SMS सामान्यतः कॉल से पहले काम करता है। तत्काल मदद के लिए नजदीकी सहायता केंद्र या पुलिस कैंप पर जाएं।",
            "en": "Mobile networks can get slow during peak crowd hours. SMS often works even when calls don't. For anything urgent, go to the nearest help desk or police camp in person.",
        },
    },
    {
        "id": "atm_location", "category": "location",
        "keywords": {
            "hi": ["ATM कहाँ है", "पैसे कैसे निकालें", "नजदीकी एटीएम"],
            "en": ["where is the nearest atm", "cash withdrawal", "atm location"],
        },
    },
    {
        "id": "luggage_storage", "category": "location",
        "keywords": {
            "hi": ["सामान कहाँ रखें", "क्लॉक रूम कहाँ है", "बैग जमा करने की जगह"],
            "en": ["where to keep luggage", "cloak room location", "baggage storage"],
        },
    },
    {
        "id": "crowd_safety_tips", "category": "static",
        "keywords": {
            "hi": ["भीड़ में सुरक्षित कैसे रहें", "भगदड़ से कैसे बचें", "सुरक्षा सलाह"],
            "en": ["how to stay safe in crowd", "avoid stampede", "safety tips"],
        },
        "draft_answer": {
            "hi": "भीड़ में बैरिकेड्स के अंदर रहें, स्वयंसेवकों/पुलिस के निर्देशों का पालन करें, बच्चों और बुजुर्गों का हाथ पकड़ें, और अगर भीड़ बहुत घनी हो तो धक्का देने के बजाय किनारे की ओर बढ़ें।",
            "en": "Stay within barricaded lanes, follow volunteer/police instructions, hold children's and elders' hands at all times, and if a crowd gets very dense, move sideways toward an edge rather than pushing forward.",
        },
    },
    {
        "id": "senior_disabled_assistance", "category": "static",
        "keywords": {
            "hi": ["व्हीलचेयर मिलेगी क्या", "बुजुर्गों के लिए सुविधा", "दिव्यांगों के लिए मदद"],
            "en": ["is a wheelchair available", "facility for senior citizens", "assistance for disabled"],
        },
        "draft_answer": {
            "hi": "वरिष्ठ नागरिकों और दिव्यांगजनों के लिए सहायता उपलब्ध है। कृपया नजदीकी सहायता केंद्र पर पहुंचकर व्हीलचेयर या सहायक की मांग करें।",
            "en": "Assistance is available for senior citizens and persons with disabilities. Please go to the nearest help desk and ask for a wheelchair or an attendant.",
        },
    },
    {
        "id": "women_helpdesk", "category": "static",
        "keywords": {
            "hi": ["महिलाओं के लिए अलग काउंटर", "महिला सहायता केंद्र", "महिला पुलिस कहाँ है"],
            "en": ["separate counter for women", "women's help desk", "where are women police"],
        },
        "draft_answer": {
            "hi": "महिला यात्रियों के लिए अलग सहायता केंद्र और महिला पुलिसकर्मी उपलब्ध हैं। किसी भी असुविधा की स्थिति में नजदीकी सहायता केंद्र या महिला हेल्पलाइन 1091 पर संपर्क करें।",
            "en": "Separate help desks and women police staff are available for women pilgrims. For any discomfort or issue, contact the nearest help desk or the women's helpline 1091.",
        },
    },
    {
        "id": "lost_child", "category": "emergency",
        "keywords": {
            "hi": ["बच्चा खो गया है", "बच्ची गुम हो गई", "बच्चा नहीं मिल रहा"],
            "en": ["my child is lost", "cannot find my child", "missing child"],
        },
        "draft_answer": {
            "hi": "घबराएं नहीं। तुरंत नजदीकी सहायता केंद्र या पुलिस कैंप पर जाएं और बच्चे का नाम, उम्र, कपड़ों का विवरण दें। मेला क्षेत्र में घोषणा प्रणाली से तुरंत सूचना दी जाएगी।",
            "en": "Don't panic. Go immediately to the nearest help desk or police camp and give the child's name, age, and clothing description. An announcement will be made across the mela area right away.",
        },
    },
    {
        "id": "fire_safety", "category": "emergency",
        "keywords": {
            "hi": ["आग लग गई", "फायर ब्रिगेड कहाँ है", "आग बुझाने की मदद"],
            "en": ["there is a fire", "where is the fire brigade", "fire emergency"],
        },
        "draft_answer": {
            "hi": "तुरंत फायर हेल्पलाइन 101 पर कॉल करें और नजदीकी सहायता केंद्र या पुलिस कैंप को सूचित करें। घबराएं नहीं, शांति से बाहर निकलने के रास्ते की ओर बढ़ें।",
            "en": "Call the fire helpline 101 immediately and alert the nearest help desk or police camp. Stay calm and move toward the nearest exit route.",
        },
    },
    {
        "id": "drinking_water_quality", "category": "static",
        "keywords": {
            "hi": ["पानी पीने लायक है क्या", "पानी साफ है क्या", "पीने का पानी सुरक्षित है"],
            "en": ["is the water safe to drink", "is the drinking water clean"],
        },
        "draft_answer": {
            "hi": "मेला प्रशासन द्वारा लगाए गए आधिकारिक पेयजल केंद्रों का पानी पीने के लिए सुरक्षित है। कृपया केवल चिन्हित पेयजल केंद्रों से ही पानी लें।",
            "en": "Water from the official drinking-water points set up by the mela administration is safe to drink. Please use only the clearly marked drinking-water points.",
        },
    },
    {
        "id": "medical_facility_general", "category": "emergency",
        "keywords": {
            "hi": ["डॉक्टर कहाँ मिलेगा", "मेडिकल कैंप कहाँ है", "दवाई कहाँ मिलेगी", "तबीयत खराब है"],
            "en": ["where can i find a doctor", "medical camp location", "where to get medicine", "i am feeling unwell"],
        },
        "draft_answer": {
            "hi": "मेला क्षेत्र में जगह-जगह निःशुल्क मेडिकल कैंप लगाए गए हैं। तबीयत खराब होने पर नजदीकी सहायता केंद्र से मेडिकल कैंप तक मार्गदर्शन लें, या एम्बुलेंस के लिए 108 डायल करें।",
            "en": "Free medical camps are set up at multiple points across the mela area. If you feel unwell, ask the nearest help desk to guide you to a medical camp, or dial 108 for an ambulance.",
        },
    },
    {
        "id": "mela_control_room", "category": "location",
        "keywords": {
            "hi": ["कंट्रोल रूम कहाँ है", "मेला प्रशासन का ऑफिस", "शिकायत कहाँ दर्ज करें"],
            "en": ["where is the control room", "mela administration office", "where to file a complaint"],
        },
    },
    {
        "id": "photography_rules", "category": "static",
        "keywords": {
            "hi": ["फोटो खींच सकते हैं क्या", "मंदिर में फोटो की अनुमति", "वीडियो बनाने की इजाजत"],
            "en": ["can i take photos", "is photography allowed in the temple", "can i record video"],
        },
    },
    {
        "id": "dress_code", "category": "static",
        "keywords": {
            "hi": ["मंदिर में कैसे कपड़े पहने", "ड्रेस कोड क्या है", "क्या पहनना चाहिए"],
            "en": ["what should i wear to the temple", "is there a dress code"],
        },
    },
]


async def main() -> None:
    await db.init_pool()
    for intent in INTENTS:
        # upsert_faq is a true INSERT ... ON CONFLICT DO UPDATE, so this is safe whether the
        # intent already exists (re-run) or is brand new -- unlike create_faq, which blind-INSERTs
        # and would fail on conflict, and which also defaults new rows to status='approved'
        # (wrong here: everything in this file must land as 'draft').
        await db.upsert_faq(PACK_ID, intent["id"], intent["category"], "draft", "claude-draft-seed")
        for lang, keywords in intent["keywords"].items():
            await db.replace_faq_keywords(PACK_ID, intent["id"], lang, keywords)
        draft_answer = intent.get("draft_answer")
        answers = draft_answer if draft_answer else PLACEHOLDER
        reviewed = bool(draft_answer)
        for lang, text in answers.items():
            await db.upsert_faq_answer(PACK_ID, intent["id"], lang, text, "claude-draft-seed", reviewed=reviewed)
        tag = "generic draft (reviewed)" if draft_answer else "placeholder (needs authority answer)"
        print(f"Seeded draft intent: {intent['id']} -- {tag}")

    ready = sum(1 for i in INTENTS if i.get("draft_answer"))
    blank = len(INTENTS) - ready
    print(f"\nDone. {len(INTENTS)} draft intents seeded into pack '{PACK_ID}'.")
    print(f"  {ready} have a generic reviewed draft answer -- admin can confirm & approve quickly.")
    print(f"  {blank} are still blank placeholders -- need a real, authority-verified answer first.")
    print("None are live -- review and approve each one at /admin/faq/<id> before they answer real pilgrims.")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
