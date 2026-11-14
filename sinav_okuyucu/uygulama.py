import io
import os
import base64
import pandas as pd
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field
from groq import Groq

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Açık Uçlu Sınav Okuma Sistemi", page_icon="📝", layout="wide")

class SoruDegerlendirme(BaseModel):
    soru_no: int = Field(description="Soru numarası")
    okunan_ogrenci_cevabi: str = Field(description="Kağıttan okunan el yazısı metni")
    yakalanan_kavramlar: list[str] = Field(description="Eş veya yakın anlamlı olarak doğru kullanılan hedef kavramlar")
    olumsuzluk_tuzagi_var_mi: bool = Field(description="Öğrenci kavramı kullanıp yanlış/olumsuz yargı kurduysa True")
    gerekce: str = Field(description="Kısa puanlama açıklaması")
    verilen_puan: float = Field(description="Bu sorudan alınan puan")

class KagitSonucu(BaseModel):
    ogrenci_ad_soyad: str = Field(description="Öğrenci adı soyadı")
    ogrenci_no: str = Field(description="Öğrenci numarası")
    sorular: list[SoruDegerlendirme]
    toplam_puan: float = Field(description="Tüm soruların puan toplamı")

def image_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    if image.mode != 'RGB': image = image.convert('RGB')
    image.thumbnail((1024, 1024))
    image.save(buffered, format="JPEG", quality=80)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

if "sorular" not in st.session_state:
    st.session_state.sorular = [
        {"no": 1, "soru_metni": "Net kuvvetin sıfırdan farklı olduğu bir cismin hareket durumunu açıklayınız.", "kavramlar": "ivmeli hareket (5p), hız değişimi (5p)", "tam_puan": 10.0},
        {"no": 2, "soru_metni": "Sürtünmesiz yatay düzlemde hareket eden cisme etki eden net kuvvet sıfırlanırsa ne olur?", "kavramlar": "sabit hızlı hareket (5p), eylemsizlik (5p)", "tam_puan": 10.0},
    ]
if "sonuclar" not in st.session_state: st.session_state.sonuclar = []

with st.sidebar:
    st.header("⚙️ Sistem Ayarları (Groq)")
    api_key = st.secrets.get("GROQ_API_KEY", "") or st.text_input("Groq API Anahtarı (gsk_ ile başlayan)", type="password")
    ders_adi = st.text_input("Ders Adı", value="Fizik / Ortak Yazılı")
    sinif_sube = st.text_input("Sınıf / Şube", value="10-A")

st.title("📝 Sınav Okuma Sistemi (⚡ Groq Hızlandırılmış)")

tab_anahtar, tab_okuma, tab_rapor = st.tabs(["1️⃣ Cevap Anahtarı", "2️⃣ Kağıt Yükle & Oku", "3️⃣ Sınıf Listesi & Excel"])

with tab_anahtar:
    if st.button("➕ Yeni Soru Ekle"):
        st.session_state.sorular.append({"no": len(st.session_state.sorular) + 1, "soru_metni": "", "kavramlar": "", "tam_puan": 10.0})
        st.rerun()
    for idx, s in enumerate(st.session_state.sorular):
        with st.expander(f"Soru {s['no']} ({s['tam_puan']} Puan)", expanded=True):
            c1, c2 = st.columns([4, 1])
            s["soru_metni"] = c1.text_input("Soru Metni", value=s["soru_metni"], key=f"soru_{idx}")
            s["tam_puan"] = c2.number_input("Tam Puan", min_value=1.0, value=float(s["tam_puan"]), key=f"puan_{idx}")
            s["kavramlar"] = st.text_area("Aranan Kavramlar", value=s["kavramlar"], key=f"kavram_{idx}")

with tab_okuma:
    yuklenen_dosyalar = st.file_uploader("Kağıtları Seçin", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
    if yuklenen_dosyalar and st.button("🚀 Kağıtları Oku (Groq AI)", type="primary"):
        if not api_key or not api_key.startswith("gsk_"):
            st.error("Lütfen geçerli bir Groq API Anahtarı girin (gsk_ ile başlamalıdır).")
        else:
            client = Groq(api_key=api_key)
            rubrik = "\n".join([f"Soru {s['no']} (Puan: {s['tam_puan']}): {s['soru_metni']} | Kavramlar: {s['kavramlar']}" for s in st.session_state.sorular])
            talimat = f"Sen bir öğretmensin. Görseldeki el yazısını oku ve hedef kavramları yakın anlamlarıyla ara. Olumsuzluk kurduysa (örn: artmaz) 0 puan ver. SADECE JSON formatında şu şemaya göre cevap ver: {{'ogrenci_ad_soyad':'', 'ogrenci_no':'', 'sorular': [{{'soru_no':1, 'okunan_ogrenci_cevabi':'', 'yakalanan_kavramlar':[''], 'olumsuzluk_tuzagi_var_mi':false, 'gerekce':'', 'verilen_puan':10.0}}], 'toplam_puan':10.0}}. CEVAP ANAHTARI: {rubrik}"
            
            yeni_sonuclar = []
            for dosya in yuklenen_dosyalar:
                with st.spinner(f"⚡ Groq Okuyor: {dosya.name}..."):
                    try:
                        gorsel = Image.open(dosya)
                        b64_gorsel = image_to_base64(gorsel)
                        yanit = client.chat.completions.create(
                            model="llama-3.2-90b-vision-instruct", # <-- RESMİ KALICI SÜRÜME GEÇİLDİ
                            messages=[{"role": "user", "content": [{"type": "text", "text": talimat}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_gorsel}"}}]}],
                            response_format={"type": "json_object"},
                            temperature=0.1,
                        )
                        sonuc_obj = KagitSonucu.model_validate_json(yanit.choices[0].message.content)
                        yeni_sonuclar.append((dosya.name, sonuc_obj))
                    except Exception as hata:
                        st.error(f"{dosya.name} okunamadı: {hata}")
            st.session_state.sonuclar = yeni_sonuclar
            st.success("Okuma tamamlandı!")

    for dosya_adi, kagit in st.session_state.sonuclar:
        with st.expander(f"📄 {kagit.ogrenci_ad_soyad} (No: {kagit.ogrenci_no}) — {kagit.toplam_puan} Puan", expanded=False):
            for s in kagit.sorular:
                st.markdown(f"**Soru {s.soru_no} ({s.verilen_puan} P):** {s.gerekce}")
                st.caption(f"✍️ *Okunan:* {s.okunan_ogrenci_cevabi}")
                if s.olumsuzluk_tuzagi_var_mi: st.warning("⚠️ Olumsuzluk Tuzağı!")

with tab_rapor:
    if st.session_state.sonuclar:
        df = pd.DataFrame([{"Okul No": k.ogrenci_no, "Ad Soyad": k.ogrenci_ad_soyad, **{f"Soru {s.soru_no}": s.verilen_puan for s in k.sorular}, "Toplam Puan": k.toplam_puan} for _, k in st.session_state.sonuclar])
        st.dataframe(df)
        tampon = io.BytesIO()
        with pd.ExcelWriter(tampon, engine="openpyxl") as yazici: df.to_excel(yazici, index=False)
        st.download_button("📥 Excel İndir", data=tampon.getvalue(), file_name=f"{sinif_sube}_Sonuclar.xlsx")
