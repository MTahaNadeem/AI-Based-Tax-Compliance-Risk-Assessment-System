"""
Synthetic Pakistani civic data generator.

Produces the exact four CSV schemas announced in the CUST/CIKLUM brief:
  fbr_tax_records.csv      (fbr_id, full_name, declared_income_pkr, tax_paid_pkr,
                            filer_status, reported_address, phone_number)
  excise_vehicles.csv      (vehicle_reg_no, owner_name, engine_capacity_cc,
                            vehicle_make_model, registration_year, owner_address)
  disco_consumption.csv    (meter_ref_no, consumer_name, installation_address,
                            avg_monthly_bill_pkr, connection_type)
  property_transfers.csv   (registry_no, buyer_name, seller_name, property_address,
                            property_value_pkr, transfer_date, area_marla, property_type)

Plus ground_truth.csv mapping every record back to its true person_id,
so the entity-resolution stage can be scored on precision/recall.

Noise injected deliberately (mirrors the brief's warnings):
  - honorific variants:  "Muhammad Ahmed" / "M. Ahmed" / "Chaudhary M. Ahmed"
  - Urdu-script names & addresses mixed with Roman transliteration
  - address format drift: "House 12, St 4, G-10/2" vs "H#12 Street-4 G10/2"
  - phone formats: 0300-1234567 / +92 300 1234567 / 03001234567
"""
import csv, random, os
from datetime import date, timedelta

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

FIRST = ["Muhammad","Ahmed","Ali","Hassan","Hussain","Usman","Bilal","Imran","Kamran",
         "Faisal","Tariq","Salman","Zeeshan","Asad","Fahad","Hamza","Shahzad","Naveed",
         "Rashid","Adnan","Waqas","Junaid","Saad","Talha","Umar","Haris","Noman","Rehan",
         "Shoaib","Danish","Arsalan","Yasir","Kashif","Nadeem","Sajid","Waseem","Irfan",
         "Zubair","Mubashir","Owais","Saqib","Atif","Babar","Ehsan","Farhan","Ghulam",
         "Ibrahim","Jawad","Khalid","Luqman","Mansoor","Nasir","Obaid","Pervez","Qasim",
         "Rauf","Sufyan","Taimoor","Ubaid","Wajahat","Yousuf","Zafar","Mustafa","Anas",
         "Ayesha","Fatima","Sana","Hina","Maryam","Zainab","Sadia","Nadia","Rabia","Amna",
         "Khadija","Mahnoor","Iqra","Laiba","Mehwish","Nimra","Saba","Tooba","Uzma","Zara",
         "Bushra","Farah","Gul","Hafsa","Javeria","Kinza","Lubna","Madiha","Noor","Rida"]
LAST  = ["Khan","Malik","Butt","Sheikh","Chaudhry","Raja","Qureshi","Siddiqui","Awan",
         "Abbasi","Mughal","Ansari","Baig","Dar","Gondal","Janjua","Kayani","Lodhi",
         "Mirza","Niazi","Bhatti","Cheema","Gill","Sandhu","Virk","Warraich","Tarar",
         "Sial","Joiya","Khokhar","Rajput","Hashmi","Kazmi","Naqvi","Rizvi","Zaidi",
         "Gilani","Bukhari","Shirazi","Yousafzai","Afridi","Khattak","Wazir","Mehsud",
         "Orakzai","Bangash","Durrani","Saddozai","Tareen","Kakar"]
HONORIFICS = ["Chaudhary","Malik","Mian","Haji","Syed","Rana","Sardar",""]

# Roman -> Urdu script lookup for noise injection
URDU = {"Muhammad":"محمد","Ahmed":"احمد","Ali":"علی","Hassan":"حسن","Hussain":"حسین",
        "Usman":"عثمان","Bilal":"بلال","Imran":"عمران","Khan":"خان","Malik":"ملک",
        "Butt":"بٹ","Sheikh":"شیخ","Chaudhry":"چوہدری","Raja":"راجہ","Qureshi":"قریشی",
        "Fatima":"فاطمہ","Ayesha":"عائشہ","Zainab":"زینب",
        "House":"مکان","Street":"گلی","Islamabad":"اسلام آباد","Rawalpindi":"راولپنڈی",
        "Lahore":"لاہور","Karachi":"کراچی"}

CITIES = ["Islamabad","Rawalpindi","Lahore","Karachi","Faisalabad","Multan","Peshawar"]
SECTORS = {"Islamabad":["G-10/2","F-8/3","I-8/4","E-11/2","G-13/1","B-17"],
           "Rawalpindi":["Satellite Town","Bahria Phase 4","Chaklala Scheme 3","Westridge"],
           "Lahore":["DHA Phase 5","Johar Town","Gulberg III","Model Town","Wapda Town"],
           "Karachi":["Clifton Block 2","DHA Phase 6","Gulshan-e-Iqbal","North Nazimabad"],
           "Faisalabad":["Peoples Colony","Madina Town","D Ground"],
           "Multan":["Cantt","Shah Rukn-e-Alam","Gulgasht"],
           "Peshawar":["Hayatabad Phase 3","University Town","Gulbahar"]}

VEHICLES = [  # (make_model, cc, approx value PKR)
    ("Suzuki Alto", 660, 2_900_000), ("Suzuki Cultus", 1000, 4_400_000),
    ("Toyota Corolla GLi", 1300, 6_000_000), ("Honda City", 1200, 5_500_000),
    ("Honda Civic RS", 1500, 9_900_000), ("Toyota Corolla Altis", 1800, 7_600_000),
    ("Hyundai Tucson", 2000, 10_500_000), ("Toyota Fortuner", 2700, 21_000_000),
    ("Toyota Land Cruiser ZX", 3300, 95_000_000), ("Toyota Prado TX", 2700, 45_000_000),
    ("Audi e-tron", 2000, 42_000_000), ("Mercedes C200", 2000, 33_000_000),
    ("Honda CD-70", 70, 160_000), ("Suzuki Mehran", 800, 1_400_000)]

PROP_TYPES = ["Residential House","Residential Plot","Commercial Plaza","Apartment","Farmhouse"]

def phone():
    n = f"3{random.randint(0,4)}{random.randint(0,9)}{random.randint(1000000,9999999)}"
    return random.choice([f"0{n[:3]}-{n[3:]}", f"+92 {n[:3]} {n[3:]}", f"0{n}"])

def maybe_urdu(text, p=0.18):
    """Transliterate some tokens to Urdu script with probability p."""
    if random.random() > p: return text
    return " ".join(URDU.get(w, w) for w in text.split())

def name_variant(first, last, style):
    if style == 0: return f"{first} {last}"
    if style == 1: return f"{first[0]}. {last}"
    if style == 2:
        h = random.choice([h for h in HONORIFICS if h])
        return f"{h} {first} {last}"
    if style == 3: return f"{first[0]}. {last}".replace(". ", ".")
    if style == 4: return maybe_urdu(f"{first} {last}", p=1.0)
    return f"{first} {last}"

def addr_variant(house, street, sector, city, style):
    if style == 0: return f"House {house}, Street {street}, {sector}, {city}"
    if style == 1: return f"H#{house} St-{street} {sector.replace(' ','')} {city}"
    if style == 2: return f"House No. {house}, Gali {street}, {sector}, {city}"
    if style == 3: return maybe_urdu(f"House {house} Street {street} {sector} {city}", p=1.0)
    return f"{house}-{street} {sector}, {city}"

BANKS = ["HBL","UBL","MCB","Meezan Bank","Bank Alfalah","Allied Bank","Askari Bank","JS Bank"]
DESTS = [("Dubai",350_000,"intl"),("London",900_000,"intl"),("Istanbul",400_000,"intl"),
         ("Baku",300_000,"intl"),("Kuala Lumpur",450_000,"intl"),("Jeddah",380_000,"intl"),
         ("Karachi",60_000,"dom"),("Lahore",40_000,"dom"),("Skardu",80_000,"dom")]

def main(n_persons=2500):
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(os.path.join(DATA, "plugins"), exist_ok=True)
    persons = []
    for pid in range(n_persons):
        first, last = random.choice(FIRST), random.choice(LAST)
        city = random.choice(CITIES)
        sector = random.choice(SECTORS[city])
        house, street = random.randint(1, 400), random.randint(1, 40)
        ph = phone()
        # archetypes: 0 honest salaried, 1 honest wealthy filer, 2 GHOST (rich non/under-filer), 3 low income
        arch = random.choices([0,1,2,3],[0.40,0.18,0.17,0.25])[0]
        if   arch == 0: income = random.randint(80_000, 350_000)*12
        elif arch == 1: income = random.randint(800_000, 4_000_000)*12
        elif arch == 2: income = random.choice([0, random.randint(30_000,90_000)*12])
        else:           income = random.randint(25_000, 70_000)*12
        persons.append(dict(pid=pid, first=first, last=last, city=city, sector=sector,
                            house=house, street=street, phone=ph, arch=arch, income=income,
                            partner_of=None))
    # benami households: ghost declarants get a same-address, same-surname partner
    # who holds the assets (the classic spouse/sibling pattern)
    partners = []
    next_pid = len(persons)
    for p in [x for x in persons if x["arch"] == 2]:
        if random.random() < 0.5:
            pf = random.choice(FIRST)
            partners.append(dict(pid=next_pid, first=pf, last=p["last"], city=p["city"],
                                 sector=p["sector"], house=p["house"], street=p["street"],
                                 phone=phone(), arch=2, income=0, partner_of=p["pid"]))
            next_pid += 1
    moiz = dict(pid=next_pid, first="Moiz", last="Judge", city="Islamabad",
                sector="F-8/3", house=12, street=4, phone="0300-1234567",
                arch=2, income=0, partner_of=None)
    persons.append(moiz)
    moiz_wife = dict(pid=next_pid + 1, first="Ayesha", last="Ahmed", city="Islamabad",
                     sector="F-8/3", house=12, street=4, phone=phone(),
                     arch=2, income=0, partner_of=next_pid)
    persons.append(moiz_wife)
    next_pid += 2
    
    #haseeb
    haseeb = dict(pid=next_pid, first="syed", last="haseeb", city="Islamabad",
                sector="F-8/3", house=12, street=4, phone="0300-1234567",
                arch=2, income=0, partner_of=None)
    persons.append(haseeb)
    haseeb_wife = dict(pid=next_pid + 1, first="sajid", last="Ahmed", city="Islamabad",
                     sector="F-8/3", house=12, street=4, phone=phone(),
                     arch=2, income=0, partner_of=next_pid)
    persons.append(haseeb_wife)
    next_pid += 3
    # ===========================================================

    persons.extend(partners)
    by_pid = {p["pid"]: p for p in persons}

    fbr, excise, disco, prop, truth = [], [], [], [], []
    banking, travel = [], []

    def record(source, row, p):
        truth.append({"source": source, "record_id": row[list(row)[0]], "person_id": p["pid"]})

    for p in persons:
        styles = random.sample(range(5), 4)
        # --- FBR (85% of people appear; ghosts often non-filer) ---
        if random.random() < 0.85:
            fid = f"FBR-{100000+p['pid']}"
            filer = "Non-Filer" if (p["arch"]==2 and random.random()<0.7) else "Filer"
            declared = 0 if filer=="Non-Filer" else p["income"]
            row = dict(fbr_id=fid,
                       full_name=name_variant(p["first"],p["last"],styles[0]),
                       declared_income_pkr=declared,
                       tax_paid_pkr=int(declared*random.uniform(0.02,0.09)),
                       filer_status=filer,
                       reported_address=addr_variant(p["house"],p["street"],p["sector"],p["city"],styles[0]),
                       phone_number=p["phone"] if random.random()<0.8 else phone())
            fbr.append(row); record("fbr", row, p)
        # --- Excise vehicles ---
        n_veh = 0
        if p["arch"] in (1,2): n_veh = random.randint(1,3)
        elif random.random() < 0.5: n_veh = 1
        for _ in range(n_veh):
            if p["arch"] in (1,2): make,cc,val = random.choice(VEHICLES[4:12])
            else:                  make,cc,val = random.choice(VEHICLES[:4]+VEHICLES[12:])
            row = dict(vehicle_reg_no=f"{random.choice(['ICT','LEA','RIR','KAR','AFR'])}-{random.randint(100,9999)}",
                       owner_name=name_variant(p["first"],p["last"],styles[1]),
                       engine_capacity_cc=cc, vehicle_make_model=make,
                       registration_year=random.randint(2017,2026),
                       owner_address=addr_variant(p["house"],p["street"],p["sector"],p["city"],styles[1]))
            row["_value"]=val
            excise.append(row); record("excise", row, p)
        # --- DISCO meter (90%) ---
        if random.random() < 0.9:
            if   p["arch"]==1: bill = random.randint(120_000, 400_000)
            elif p["arch"]==2: bill = random.randint(150_000, 450_000)
            elif p["arch"]==0: bill = random.randint(15_000, 60_000)
            else:              bill = random.randint(3_000, 18_000)
            row = dict(meter_ref_no=f"MTR-{random.randint(10_000_000,99_999_999)}",
                       consumer_name=name_variant(p["first"],p["last"],styles[2]),
                       installation_address=addr_variant(p["house"],p["street"],p["sector"],p["city"],styles[2]),
                       avg_monthly_bill_pkr=bill,
                       connection_type=random.choice(["Domestic","Domestic","Commercial"]))
            disco.append(row); record("disco", row, p)
        # --- Property ---
        n_prop = 0
        if p["arch"] in (1,2): n_prop = random.randint(1,2)
        elif random.random() < 0.25: n_prop = 1
        for _ in range(n_prop):
            marla = random.choice([5,7,10,20,40,80]) if p["arch"] in (1,2) else random.choice([3,5,7])
            val = marla * random.randint(1_800_000, 9_000_000)
            if p["partner_of"] is not None and random.random() < 0.7:
                pr = by_pid[p["partner_of"]]
                seller = f"{pr['first']} {pr['last']}"      # intra-household transfer
            else:
                seller = f"{random.choice(FIRST)} {random.choice(LAST)}"
            d = date(2021,1,1)+timedelta(days=random.randint(0,1900))
            row = dict(registry_no=f"REG-{random.randint(100000,999999)}",
                       buyer_name=name_variant(p["first"],p["last"],styles[3]),
                       seller_name=seller,
                       property_address=addr_variant(random.randint(1,400),random.randint(1,40),
                                                     random.choice(SECTORS[p["city"]]),p["city"],0),
                       property_value_pkr=val, transfer_date=d.isoformat(),
                       area_marla=marla, property_type=random.choice(PROP_TYPES))
            prop.append(row); record("property", row, p)

        # --- BANKING plug-in (optional 5th dataset) ---
        n_acc = 0
        if   p["arch"] == 1: n_acc = random.randint(1, 2)
        elif p["arch"] == 2: n_acc = random.randint(2, 4)        # structuring pattern
        elif random.random() < 0.6: n_acc = 1
        for _ in range(n_acc):
            if   p["arch"] == 1: dep = random.randint(800_000, 5_000_000)
            elif p["arch"] == 2: dep = random.randint(1_200_000, 8_000_000)
            else:                dep = int(p["income"] / 12 * random.uniform(0.6, 1.1))
            row = dict(account_no=f"PK{random.randint(10,99)}-{random.randint(10**9,10**10-1)}",
                       holder_name=name_variant(p["first"], p["last"], random.choice(range(5))),
                       holder_address=addr_variant(p["house"], p["street"], p["sector"], p["city"],
                                                   random.choice(range(4))),
                       bank_name=random.choice(BANKS),
                       account_type=random.choice(["Current","Savings","Savings","Foreign Currency"]),
                       opened_date=(date(2018,1,1)+timedelta(days=random.randint(0,2900))).isoformat(),
                       avg_monthly_deposit_pkr=dep)
            banking.append(row); record("banking", row, p)
        # --- TRAVEL plug-in (optional 6th dataset) ---
        n_trips = 0
        if   p["arch"] == 1: n_trips = random.randint(1, 4)
        elif p["arch"] == 2: n_trips = random.randint(2, 6)
        elif random.random() < 0.25: n_trips = 1
        for _ in range(n_trips):
            if p["arch"] in (1, 2): dest, cost, _k = random.choice(DESTS[:6])
            else:                   dest, cost, _k = random.choice(DESTS[6:])
            row = dict(trip_id=f"TRP-{random.randint(10**6,10**7-1)}",
                       passenger_name=name_variant(p["first"], p["last"], random.choice(range(5))),
                       passenger_address=addr_variant(p["house"], p["street"], p["sector"], p["city"],
                                                      random.choice(range(4))),
                       destination=dest,
                       departure_date=(date(2024,1,1)+timedelta(days=random.randint(0,880))).isoformat(),
                       trip_class=random.choice(["Business","First"]) if p["arch"] in (1,2)
                                  else "Economy",
                       est_trip_cost_pkr=int(cost*random.uniform(0.85,1.5)))
            travel.append(row); record("travel", row, p)

    def dump(name, rows, drop=(), sub=""):
        if not rows: return
        cols = [c for c in rows[0] if c not in drop]
        with open(os.path.join(DATA, sub, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)

    dump("fbr_tax_records.csv", fbr)
    dump("excise_vehicles.csv", excise, drop=("_value",))
    dump("disco_consumption.csv", disco)
    dump("property_transfers.csv", prop)
    dump("ground_truth.csv", truth)
    dump("banking_accounts.csv", banking, sub="plugins")
    dump("travel_logs.csv", travel, sub="plugins")
    print(f"persons={len(persons)} fbr={len(fbr)} excise={len(excise)} "
          f"disco={len(disco)} property={len(prop)} | plugins: banking={len(banking)} "
          f"travel={len(travel)}")

if __name__ == "__main__":
    main()
