// app/static/i18n.js

const translations = {
    // Navigation
    "Dashboard": "ڈیش بورڈ",
    "Risk Register": "رسک رجسٹر",
    "Integrations": "انضمام",
    "Citizen Disputes": "شہریوں کے تنازعات",
    "Control Panel": "کنٹرول پینل",
    "Add Person": "شخص شامل کریں",
    "Log out": "لاگ آؤٹ",

    // Add Person View - Headers & Labels
    "Personal Information": "ذاتی معلومات",
    "Name (as per CNIC)": "نام (CNIC کے مطابق)",
    "CNIC (13 digits)": "شناختی کارڈ نمبر (13 ہندسے)",
    "Father / Husband Name": "والد / شوہر کا نام",
    "Date of Birth": "تاریخ پیدائش",
    "Gender": "صنف",
    "Male": "مرد",
    "Female": "عورت",
    "Other": "دیگر",
    "Contact Information": "رابطہ کی معلومات",
    "Primary Phone Number": "بنیادی فون نمبر",
    "Email Address": "ای میل ایڈریس",
    "Residential Address": "رہائشی پتہ",
    "Tax & FBR Status": "ٹیکس اور FBR کی حیثیت",
    "NTN (Optional)": "NTN (اختیاری)",
    "Current Filer Status": "موجودہ فائلر کی حیثیت",
    "Filer": "فائلر",
    "Non-Filer": "نان فائلر",
    "Unknown": "نامعلوم",
    
    // Add Person View - Assets & Vehicles
    "Vehicles (Excise Data)": "گاڑیاں (ایکسائز ڈیٹا)",
    "Add Vehicle": "گاڑی شامل کریں",
    "Registration Number": "رجسٹریشن نمبر",
    "Make / Model": "میک / ماڈل",
    "Engine Capacity (cc)": "انجن کی صلاحیت (cc)",
    "Registration Date": "تاریخِ رجسٹریشن",
    
    // Add Person View - Utilities
    "Utilities (DISCO / Sui Gas)": "یوٹیلیٹیز (ڈسکو / سوئی گیس)",
    "Add Utility Connection": "یوٹیلیٹی کنکشن شامل کریں",
    "Connection / Ref Number": "کنکشن / حوالہ نمبر",
    "Meter Number": "میٹر نمبر",
    "Tariff Category": "ٹیرف کیٹیگری",
    "Avg Monthly Bill (PKR)": "اوسط ماہانہ بل (PKR)",
    "Connection Date": "کنکشن کی تاریخ",
    
    // Add Person View - Properties
    "Properties (Registry)": "جائیداد (رجسٹری)",
    "Add Property": "جائیداد شامل کریں",
    "Property Address": "جائیداد کا پتہ",
    "Property Type": "جائیداد کی قسم",
    "Commercial": "تجارتی",
    "Residential": "رہائشی",
    "Agricultural": "زرعی",
    "Area (Marla)": "رقبہ (مرلہ)",
    "Assessed Value (PKR)": "تخمینی مالیت (PKR)",
    "Transfer Date": "منتقلی کی تاریخ",
    
    // Add Person View - FBR Tax Returns
    "FBR Tax Returns": "FBR ٹیکس ریٹرن",
    "Add Tax Return": "ٹیکس ریٹرن شامل کریں",
    "Tax Year": "ٹیکس سال",
    "Return Reference No.": "ریٹرن حوالہ نمبر",
    "Declared Income (PKR)": "اعلان کردہ آمدنی (PKR)",
    "Tax Paid (PKR)": "ادا کردہ ٹیکس (PKR)",
    
    // Add Person View - Configuration
    "Configuration & Submit": "ترتیب اور جمع کروائیں",
    "Assign to existing household": "موجودہ گھرانے میں شامل کریں",
    "Search Household ID...": "گھرانے کی آئی ڈی تلاش کریں...",
    "Provision a Citizen Portal login for this person": "اس شخص کے لیے سٹیزن پورٹل لاگ ان فراہم کریں",
    "Requires a valid phone number.": "درست فون نمبر کی ضرورت ہے۔",
    "Temporary Password": "عارضی پاس ورڈ",
    "Override Identity Matching (Force Create)": "شناخت کی جانچ کو نظر انداز کریں (زبردستی بنائیں)",
    "Skip the duplicate check if you are certain this is a new citizen.": "اگر آپ کو یقین ہے کہ یہ نیا شہری ہے تو ڈپلیکیٹ چیک چھوڑ دیں۔",
    "Search & Add Person": "تلاش کریں اور شخص شامل کریں",

    "TaxNet Graph": "قومی ٹیکس نیٹ — گراف انٹیلی جنس",
    "Identity · required": "شناخت",
    "Additional Details · optional": "مزید تفصیل",
    "Tax Record · optional": "ٹیکس ریکارڈ",
    "Vehicle / Excise · optional": "گاڑیوں کا ریکارڈ",
    "Utilities / DISCO · optional": "بجلی کا ریکارڈ",
    "Property · optional": "جائیداد کا ریکارڈ",
    "Household Link · optional": "خاندانی ربط",
    "Portal Access": "پورٹل رسائی",
    "Full Name *": "پورا نام *",
    "Mobile Phone *": "موبائل فون *",
    "Address *": "پتہ *",

    // Alerts
    "Person successfully added.": "شخص کامیابی سے شامل ہو گیا۔",
    "Identity Match Warning": "شناخت کی مماثلت کی وارننگ",
    
    // Header labels
    "Administrator": "ایڈمنسٹریٹر",
    
    // Household association
    "No household selected": "کوئی گھرانہ منتخب نہیں کیا گیا"
};

// State
window.CURRENT_LANG = localStorage.getItem('site_lang') || 'en';

// Apply language immediately before rendering
document.documentElement.lang = window.CURRENT_LANG;
if (window.CURRENT_LANG === 'ur') {
    document.documentElement.dir = 'rtl';
} else {
    document.documentElement.dir = 'ltr';
}

// Global translation helper
window.t = function(key) {
    if (!key) return key;
    if (window.CURRENT_LANG === 'ur' && translations[key]) {
        return translations[key];
    }
    return key;
};

// Global setter
window.setLanguage = function(lang) {
    window.CURRENT_LANG = lang;
    localStorage.setItem('site_lang', lang);
    document.documentElement.lang = lang;
    if (lang === 'ur') {
        document.documentElement.dir = 'rtl';
    } else {
        document.documentElement.dir = 'ltr';
    }
    
    // Update the visual switcher if it exists
    const switcher = document.getElementById('lang-switcher');
    if (switcher) {
        switcher.value = lang;
    }
    
    // Re-render the view
    if (window.app && typeof window.app.render === 'function') {
        window.app.render(); // This relies on the SPA framework pattern, we may also need to update static HTML.
    } else if (window.render) {
        window.render();
    }
    
    // Refresh page for simplest full re-render if SPA render isn't fully comprehensive for static elements
    // We will do a full reload for simplicity to ensure all DOM, including nav elements, are re-translated,
    // UNLESS we are dynamically updating everything. For now, let's try a full reload as it is very reliable 
    // for translating the entire page (nav, header, etc.) if they aren't generated by app.render().
    // Actually, since it's a SPA, reloading might lose transient state, so let's try to update dynamically.
    translateStaticDOM();
};

function translateStaticDOM() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        // Handle elements that have mixed content (icons + text)
        if (el.tagName === 'INPUT' && (el.type === 'text' || el.type === 'password' || el.type === 'number')) {
            el.placeholder = window.t(key);
        } else {
            // Check if there's an icon inside, if so preserve it.
            const icon = el.querySelector('i, svg, span.material-icons');
            if (icon) {
                // If the element has an icon, we assume text is in a text node or the icon is first.
                // Replace text nodes, keeping elements intact
                let translated = false;
                for (let i = 0; i < el.childNodes.length; i++) {
                    let node = el.childNodes[i];
                    if (node.nodeType === Node.TEXT_NODE && node.nodeValue.trim() !== '') {
                        node.nodeValue = " " + window.t(key) + " ";
                        translated = true;
                        break;
                    }
                }
                if (!translated) {
                    // Fallback
                    el.innerHTML = icon.outerHTML + " " + window.t(key);
                }
            } else {
                el.textContent = window.t(key);
            }
        }
    });
}

// Ensure static elements are translated on load
document.addEventListener('DOMContentLoaded', () => {
    translateStaticDOM();
    
    const switcher = document.getElementById('lang-switcher');
    if (switcher) {
        switcher.value = window.CURRENT_LANG;
        switcher.addEventListener('change', (e) => {
            window.setLanguage(e.target.value);
            // Since our nav and static HTML is outside the SPA render loop, let's just reload the page 
            // for now to guarantee 100% translation if translateStaticDOM isn't perfect.
            // Wait, we have translateStaticDOM, so it should be fine. But re-rendering the SPA is needed.
            if (window.app && window.app.currentRoute) {
               window.location.reload(); 
            }
        });
    }
});
