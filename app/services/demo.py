"""Realistic demo data for trying ClassNest:  python -m app.cli demo-data [--students 36] [--clear]

Creates a small tuition centre: teaching and support staff, 8 batches (Class 8 / 10 / 12) that respect the batch rules,
students with generated photos, ID proofs and real ClassNest ID cards, parents, health notes, fees for two months,
last month's salaries, two weeks of past classes with attendance and class notes (indexed, so the AI tutor answers
from them), upcoming live classes, homework with submissions and grades, a published test per batch with graded
attempts, a class video with comments and replies (Class 10 Maths), batch-chat messages and a few website enquiries.

* Every demo account signs in with the password DEMO_PASSWORD (printed at the end) - remove them before real use.
* Demo people get e-mail addresses at demo.classnest.test (a reserved, undeliverable domain), so mail to them goes
  nowhere; admin alerts about demo students (e.g. `--with-insights`) do reach ADMIN_ALERT_EMAILS.
* Everything is tagged, so `--clear` removes exactly the demo data (and its files) and nothing else.
"""
from __future__ import annotations

import colorsys
import datetime as dt
import io
import random
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (Attendance, Batch, BatchChatMessage, ContactMessage, Enrollment, FeePayment, HealthRecord,
                      Lesson, Meeting, Question, SalaryPayment, Task, TaskSubmission, Test, TestAttempt, User, Video,
                      VideoComment)
from ..security import hash_password
from .academic_calendar import classify, holidays_between
from .idcard import font
from .meetings import new_code
from .people import issue_id_card, make_id_code, unique_username
from .rag import index_document
from .scheduling import validate_batch
from .storage import delete_file, save_bytes

DEMO_TAG = "demo-data"             # User.created_by of every demo account
DEMO_MARK = "[demo]"               # prefix of demo batch descriptions
DEMO_DOMAIN = "demo.classnest.test"
DEMO_PASSWORD = "Demo@12345"

BOYS = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan", "Shaurya",
        "Atharv", "Kabir", "Rohan", "Dhruv", "Arnav", "Kartik", "Yash", "Rudra", "Pranav", "Aryan", "Neel"]
GIRLS = ["Aadhya", "Diya", "Ananya", "Saanvi", "Myra", "Ira", "Anika", "Navya", "Kiara", "Pari", "Riya", "Meera",
         "Aarohi", "Sara", "Ishita", "Tanvi", "Kavya", "Nisha", "Pooja", "Zara", "Advika", "Mahika"]
SURNAMES = ["Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Gupta", "Mehta", "Joshi", "Kulkarni",
            "Desai", "Banerjee", "Chatterjee", "Das", "Singh", "Kapoor", "Menon", "Pillai", "Rao", "Khan",
            "Fernandes", "Mishra", "Pandey", "Bose", "Saxena", "Agarwal", "Bhat"]
PARENTS_M = ["Rajesh", "Suresh", "Amit", "Sanjay", "Vikas", "Manoj", "Deepak", "Arvind", "Rahul", "Nitin"]
PARENTS_F = ["Priya", "Sunita", "Anjali", "Kavita", "Neha", "Rekha", "Pooja", "Lakshmi", "Shalini", "Meenakshi"]
SCHOOLS = ["Kendriya Vidyalaya No. 2", "Delhi Public School", "St. Xavier's High School", "Ryan International School",
           "DAV Public School", "Sardar Patel Vidyalaya"]
CITIES = ["Andheri West, Mumbai", "Koramangala, Bengaluru", "Salt Lake, Kolkata", "Anna Nagar, Chennai",
          "Kothrud, Pune", "Banjara Hills, Hyderabad", "Dwarka, New Delhi"]

# name, staff_role, qualification, monthly salary, gender, teaches (subject key) or None
STAFF = [
    ("Dr. Meera Iyer", "principal", "M.Sc., Ph.D. (Mathematics)", 85000, "Female", None),
    ("Rakesh Kulkarni", "coordinator", "M.A., B.Ed.", 55000, "Male", None),
    ("Sunita Rao", "teacher", "M.Sc. Mathematics, B.Ed.", 45000, "Female", "maths"),
    ("Kavitha Menon", "teacher", "M.Sc. Biology, B.Ed.", 44000, "Female", "science"),
    ("Joseph Fernandes", "teacher", "M.A. English Literature", 42000, "Male", "english"),
    ("Anil Deshpande", "teacher", "M.Sc. Physics", 46000, "Male", "physics"),
    ("Farah Khan", "teacher", "M.Sc. Chemistry", 45000, "Female", "chemistry"),
    ("Vikram Singh", "sme", "B.Tech. Computer Science", 50000, "Male", "cs"),
    ("Neha Gupta", "counselor", "M.A. Psychology", 38000, "Female", None),
    ("Prakash Shah", "accountant", "B.Com., Tally certified", 35000, "Male", None),
    ("Lata Pillai", "librarian", "M.Lib.Sc.", 30000, "Female", None),
    ("Ramesh Yadav", "cleaner", "", 15000, "Male", None),
    ("Savitri Devi", "cleaner", "", 15000, "Female", None),
    ("Gopal Thapa", "security", "", 18000, "Male", None),
]
# name, class, subject key, days (0=Mon), start, end, monthly fee, colour
BATCHES = [
    ("Class 8 · Mathematics", "Class 8", "maths", [0, 2, 4], "16:00", "17:00", 1500, "#6366f1"),
    ("Class 8 · Science", "Class 8", "science", [1, 3, 5], "16:00", "17:00", 1500, "#10b981"),
    ("Class 10 · Mathematics", "Class 10", "maths", [0, 2, 4], "17:15", "18:15", 1800, "#8b5cf6"),
    ("Class 10 · Science", "Class 10", "science", [1, 3, 5], "17:15", "18:15", 1800, "#14b8a6"),
    ("Class 10 · English", "Class 10", "english", [0, 2], "18:30", "19:30", 1200, "#f59e0b"),
    ("Class 12 · Physics", "Class 12", "physics", [0, 2, 4], "17:15", "18:15", 2200, "#3b82f6"),
    ("Class 12 · Chemistry", "Class 12", "chemistry", [1, 3, 5], "17:15", "18:15", 2200, "#ef4444"),
    ("Class 12 · Computer Science", "Class 12", "cs", [1, 3], "18:30", "19:30", 2000, "#0ea5e9"),
]
SUBJECTS = {"maths": "Mathematics", "science": "Science", "english": "English", "physics": "Physics",
            "chemistry": "Chemistry", "cs": "Computer Science"}
TOPICS = {
    "maths": ["Rational numbers", "Linear equations", "Quadratic equations", "Triangles & congruence",
              "Mensuration", "Probability", "Statistics", "Algebraic identities"],
    "science": ["Photosynthesis", "Force & pressure", "Cell structure", "Sound", "Chemical reactions",
                "Human digestive system", "Light & reflection", "Electricity"],
    "english": ["Tenses", "Active & passive voice", "Letter writing", "Poetry appreciation", "Reported speech",
                "Essay structure"],
    "physics": ["Kinematics", "Laws of motion", "Work, energy & power", "Current electricity", "Ray optics",
                "Waves"],
    "chemistry": ["Atomic structure", "Chemical bonding", "Periodic table", "Acids, bases & salts",
                  "Organic chemistry basics", "Chemical equilibrium"],
    "cs": ["Python basics", "Lists & dictionaries", "Functions", "Searching & sorting", "SQL queries",
           "File handling"],
}
# Class notes for the past demo classes. They are indexed into the knowledge base, so the AI tutor answers from
# them like it would from real class notes - keep them correct: a small model repeats what it is given.
TOPIC_NOTES = {
    "Rational numbers": "A rational number can be written as p/q, where p and q are integers and q ≠ 0 - for example "
    "3/4, -2/5, 7 (= 7/1) and 0.5 (= 1/2). Every integer is rational. To add fractions, first make the denominators "
    "equal: 3/4 + 1/8 = 6/8 + 1/8 = 7/8. Between any two rational numbers there are infinitely many others.",
    "Linear equations": "A linear equation in one variable has the form ax + b = c (a ≠ 0) and exactly one solution. "
    "Do the same operation on both sides to isolate x: 2x + 5 = 17 → 2x = 12 → x = 6. Always check the answer by "
    "substituting it back into the equation.",
    "Quadratic equations": "A quadratic equation has the form ax² + bx + c = 0 with a ≠ 0. Solve it by factorising "
    "(x² - 5x + 6 = (x - 2)(x - 3), so x = 2 or x = 3) or with the formula x = (-b ± √(b² - 4ac)) / 2a. The "
    "discriminant D = b² - 4ac tells the roots: D > 0 two real roots, D = 0 one repeated root, D < 0 no real roots.",
    "Triangles & congruence": "The angles of a triangle add up to 180°. Two triangles are congruent when they have "
    "the same shape and size; the tests are SSS, SAS, ASA, AAS and RHS (right angle-hypotenuse-side). Corresponding "
    "parts of congruent triangles are equal (CPCT).",
    "Mensuration": "Rectangle: area = length × breadth (8 cm × 5 cm = 40 cm²), perimeter = 2(l + b). Triangle: "
    "area = ½ × base × height. Circle: circumference = 2πr, area = πr². Volume of a cuboid = l × b × h and of a "
    "cylinder = πr²h.",
    "Probability": "Probability of an event = number of favourable outcomes ÷ total number of equally likely "
    "outcomes, so it always lies between 0 and 1. A fair die shows an even number with probability 3/6 = 1/2. "
    "P(not E) = 1 - P(E).",
    "Statistics": "Mean = sum of the values ÷ number of values. Median = the middle value after sorting (the average "
    "of the two middle values when the count is even). Mode = the most frequent value. For 2, 3, 3, 5, 7: mean = "
    "20 ÷ 5 = 4, median = 3, mode = 3.",
    "Algebraic identities": "(a + b)² = a² + 2ab + b²; (a - b)² = a² - 2ab + b²; (a + b)(a - b) = a² - b². "
    "Example: 103² = (100 + 3)² = 10000 + 600 + 9 = 10609. An identity is true for every value of the variables.",
    "Photosynthesis": "Green plants make their own food (glucose) from carbon dioxide and water, using sunlight "
    "absorbed by chlorophyll in the leaves, and release oxygen: carbon dioxide + water → glucose + oxygen. Gases "
    "enter and leave the leaf through tiny pores called stomata.",
    "Force & pressure": "A force is a push or a pull; its SI unit is the newton (N). Pressure = force ÷ area, in "
    "pascals (1 Pa = 1 N/m²) - the same force on a smaller area gives more pressure, which is why knives are sharp. "
    "Liquids and gases exert pressure too, and atmospheric pressure acts on us all the time.",
    "Cell structure": "The cell is the basic unit of life. Plant and animal cells both have a cell membrane, cytoplasm "
    "and a nucleus that holds the DNA; mitochondria release energy. Plant cells also have a cell wall, chloroplasts "
    "and a large vacuole.",
    "Sound": "Sound is produced by vibrating objects and needs a medium (solid, liquid or gas) - it cannot travel "
    "through a vacuum. In air at room temperature it travels at about 343 m/s, faster in water and in solids. Pitch "
    "depends on frequency (hertz); loudness depends on amplitude.",
    "Chemical reactions": "In a chemical reaction new substances form; signs are a change of colour, a gas given off, "
    "a temperature change or a precipitate. Mass is conserved, so equations are balanced: 2H₂ + O₂ → 2H₂O. Types "
    "include combination, decomposition, displacement and redox reactions.",
    "Human digestive system": "Digestion breaks food into small soluble molecules: mouth (saliva starts digesting "
    "starch) → oesophagus → stomach (acid and pepsin start on proteins) → small intestine (most digestion, and "
    "absorption through villi) → large intestine (water is absorbed). The liver makes bile and the pancreas makes "
    "digestive enzymes.",
    "Light & reflection": "Light travels in straight lines. Laws of reflection: the angle of incidence equals the "
    "angle of reflection, and the incident ray, the reflected ray and the normal lie in the same plane. A plane "
    "mirror forms a virtual, upright, same-size image that is laterally inverted and as far behind the mirror as "
    "the object is in front.",
    "Electricity": "Electric current is the flow of charge, measured in amperes (A); potential difference is "
    "measured in volts (V). Ohm's law: V = I × R, with resistance in ohms (Ω). In series R = R₁ + R₂; in parallel "
    "1/R = 1/R₁ + 1/R₂. Power P = V × I, in watts.",
    "Tenses": "Tense shows when an action happens: simple present for habits and facts (She reads every day), present "
    "continuous for actions happening now (She is reading), simple past for finished actions (She read yesterday), "
    "present perfect for past actions linked to now (She has read the book) and the future with will (She will "
    "read).",
    "Active & passive voice": "In the active voice the subject does the action (The cat chased the mouse); in the "
    "passive voice the subject receives it (The mouse was chased by the cat). Form the passive with 'be' in the same "
    "tense + the past participle: They are building a bridge → A bridge is being built.",
    "Letter writing": "A formal letter has the sender's address, the date, the receiver's address, a subject line, a "
    "salutation (Dear Sir/Madam), the body, a complimentary close (Yours faithfully / Yours sincerely) and a "
    "signature; keep it polite and to the point. An informal letter to friends or family can be conversational.",
    "Poetry appreciation": "To appreciate a poem, discuss its theme, tone and mood and its devices: simile (as brave as "
    "a lion), metaphor (time is a thief), personification (the wind whispered), alliteration (silver snakes slid) "
    "and the rhyme scheme (e.g. AABB). Support every point with a short quotation.",
    "Reported speech": "Reported (indirect) speech tells what someone said without quoting them. The tense usually "
    "moves one step back and pronouns and time words change: She said, 'I am tired.' → She said that she was tired; "
    "'today' becomes 'that day'. Questions become statements: He asked, 'Where do you live?' → He asked where I "
    "lived.",
    "Essay structure": "An essay has an introduction (a hook and the main idea), body paragraphs (one point each, "
    "with a topic sentence, explanation and example) and a conclusion that sums up without new points. Plan first "
    "and link paragraphs with connectives such as however, moreover and therefore.",
    "Kinematics": "Velocity = displacement ÷ time; acceleration = change in velocity ÷ time. For constant "
    "acceleration: v = u + at, s = ut + ½at², v² = u² + 2as. Example: a car starting from rest with a = 2 m/s² "
    "reaches v = 10 m/s after 5 s.",
    "Laws of motion": "First law: a body stays at rest or in uniform motion unless an unbalanced force acts on it "
    "(inertia). Second law: F = ma, with force in newtons. Third law: every action has an equal and opposite "
    "reaction. Momentum p = mv is conserved when no external force acts.",
    "Work, energy & power": "Work W = F × s × cos θ, in joules (J). Kinetic energy = ½mv²; potential energy near the "
    "Earth's surface = mgh. Energy is conserved - it only changes form. Power = work ÷ time, in watts (1 W = 1 J/s).",
    "Current electricity": "Current I = Q/t, in amperes. Ohm's law V = IR holds for ohmic conductors at constant "
    "temperature. Kirchhoff's current law: current into a junction = current out; voltage law: the potential "
    "changes around any closed loop add up to zero. Power P = VI = I²R = V²/R.",
    "Ray optics": "Mirror formula 1/v + 1/u = 1/f, with f = R/2. Refraction follows Snell's law n₁ sin i = n₂ sin r. "
    "Lens formula 1/v - 1/u = 1/f (Cartesian sign convention); lens power P = 1/f with f in metres, in dioptres. "
    "Total internal reflection needs light going from a denser to a rarer medium beyond the critical angle.",
    "Waves": "A wave transfers energy without transferring matter. Wave speed v = fλ. Transverse waves vibrate at "
    "right angles to the direction of travel (light, waves on a string); longitudinal waves vibrate along it "
    "(sound). Superposition gives interference, standing waves and beats.",
    "Atomic structure": "An atom has a nucleus of protons (positive) and neutrons (neutral), with electrons "
    "(negative) around it. Atomic number Z = number of protons; mass number A = protons + neutrons. Isotopes have "
    "the same Z but a different A (carbon-12 and carbon-14). Electrons fill orbitals in order of increasing energy "
    "(Aufbau principle).",
    "Chemical bonding": "Ionic bonds form when electrons are transferred (Na⁺Cl⁻); covalent bonds form when electrons "
    "are shared (H₂O, CH₄). Atoms bond to reach a stable octet. VSEPR theory predicts shapes: CH₄ is tetrahedral "
    "(109.5°), NH₃ is trigonal pyramidal and H₂O is bent (about 104.5°).",
    "Periodic table": "Elements are arranged by increasing atomic number. Across a period the atomic radius decreases "
    "and ionisation energy and electronegativity generally increase; down a group the atomic radius increases. "
    "Group 1 holds the alkali metals, group 17 the halogens and group 18 the noble gases.",
    "Acids, bases & salts": "Acids give H⁺ ions in water (pH below 7); bases give OH⁻ ions (pH above 7); pH 7 is "
    "neutral. Acid + base → salt + water (neutralisation), e.g. HCl + NaOH → NaCl + H₂O. Indicators such as "
    "litmus and phenolphthalein show whether a solution is acidic or basic.",
    "Organic chemistry basics": "Organic chemistry is the chemistry of carbon compounds. Carbon is tetravalent and "
    "forms chains and rings (catenation). Members of a homologous series differ by CH₂: alkanes CₙH₂ₙ₊₂ (methane "
    "CH₄), alkenes CₙH₂ₙ (ethene C₂H₄). Functional groups such as -OH (alcohols) and -COOH (carboxylic acids) decide "
    "the properties.",
    "Chemical equilibrium": "In a reversible reaction, equilibrium is reached when the forward and backward rates are "
    "equal, so the concentrations stay constant. For aA + bB ⇌ cC + dD, Kc = [C]^c[D]^d / ([A]^a[B]^b). Le "
    "Chatelier's principle: when concentration, pressure or temperature changes, the equilibrium shifts to oppose "
    "the change.",
    "Python basics": "Python runs code line by line; a variable is created by assignment (x = 5) and indentation "
    "marks code blocks. Core types are int, float, str and bool. Use if/elif/else for decisions, for and while "
    "loops for repetition, input() to read text and print() to show output; int('7') turns text into a number.",
    "Lists & dictionaries": "A list is an ordered, changeable sequence: marks = [72, 85, 90]; marks[0] is 72 and "
    "marks.append(66) adds an item. A dictionary maps keys to values: student = {'name': 'Asha', 'class': 12}; "
    "student['name'] gives 'Asha'. Lists are indexed by position, dictionaries by key.",
    "Functions": "A function is a named, reusable block of code: def area(l, b): return l * b. Parameters receive "
    "the arguments passed in, and return sends a result back (without return a function gives None). Variables "
    "created inside a function are local to it.",
    "Searching & sorting": "Linear search checks the items one by one (O(n)); binary search needs sorted data and "
    "halves the range at every step (O(log n)). Bubble sort repeatedly swaps neighbouring items that are out of "
    "order (O(n²)); Python's sorted() and list.sort() are far faster in practice.",
    "SQL queries": "SELECT name, marks FROM students WHERE marks > 80 ORDER BY marks DESC; filters and sorts rows. "
    "COUNT, SUM, AVG, MIN and MAX summarise rows, usually with GROUP BY. INSERT adds rows, UPDATE changes them and "
    "DELETE removes them; a PRIMARY KEY identifies each row uniquely.",
    "File handling": "open(path, mode) opens a file: 'r' read, 'w' write (overwrites), 'a' append. A with block "
    "closes it automatically: with open('data.txt') as f: text = f.read(). readline() reads one line and "
    "readlines() returns a list of lines; use binary mode and the pickle module to store Python objects.",
}
# (question, options, index of the correct option, explanation)
QUESTIONS = {
    "maths": [("What is 3/4 + 1/8?", ["7/8", "4/12", "1/2", "5/8"], 0, "3/4 = 6/8, and 6/8 + 1/8 = 7/8."),
              ("Solve: 2x + 5 = 17", ["x = 6", "x = 11", "x = 8.5", "x = 5"], 0, "2x = 12, so x = 6."),
              ("The angles of a triangle add up to…", ["90°", "180°", "270°", "360°"], 1, "Angle-sum property."),
              ("Which of these is a prime number?", ["21", "27", "29", "33"], 2, "29 has only the factors 1 and 29."),
              ("Area of a rectangle 8 cm × 5 cm?", ["13 cm²", "26 cm²", "40 cm²", "80 cm²"], 2, "8 × 5 = 40.")],
    "science": [("Which gas do plants take in for photosynthesis?", ["Oxygen", "Carbon dioxide", "Nitrogen",
                                                                      "Hydrogen"], 1, "Plants absorb CO₂."),
                ("The SI unit of force is…", ["Joule", "Watt", "Newton", "Pascal"], 2, "1 N = 1 kg·m/s²."),
                ("Which organ pumps blood around the body?", ["Lungs", "Liver", "Heart", "Kidney"], 2,
                 "The heart is a muscular pump."),
                ("At sea level, water boils at…", ["50 °C", "90 °C", "100 °C", "120 °C"], 2, "At 1 atm."),
                ("Which of these conducts electricity?", ["Rubber", "Copper", "Plastic", "Wood"], 1,
                 "Copper is a metal with free electrons.")],
    "english": [("Choose the correct spelling:", ["Recieve", "Receive", "Receeve", "Riceive"], 1,
                 "'i' before 'e' except after 'c'."),
                ("Find the noun: 'The happy child sang loudly.'", ["happy", "child", "sang", "loudly"], 1,
                 "'child' names a person."),
                ("The past tense of 'write' is…", ["writed", "wrote", "written", "writes"], 1,
                 "'written' is the past participle."),
                ("The antonym of 'ancient' is…", ["old", "modern", "historic", "antique"], 1, "Opposite meaning."),
                ("Which sentence is in the passive voice?", ["The cat chased the mouse.",
                                                             "The mouse was chased by the cat.",
                                                             "The cat is chasing the mouse.",
                                                             "The cat will chase the mouse."], 1,
                 "The subject receives the action.")],
    "physics": [("The SI unit of electric current is…", ["Volt", "Ohm", "Ampere", "Coulomb"], 2, "Symbol A."),
                ("The speed of light in vacuum is about…", ["3 × 10⁸ m/s", "3 × 10⁶ m/s", "3 × 10⁵ m/s",
                                                            "340 m/s"], 0, "c ≈ 3 × 10⁸ m/s."),
                ("Ohm's law: V = …", ["I / R", "I × R", "I + R", "R / I"], 1, "V = IR."),
                ("Which quantity is a vector?", ["Mass", "Speed", "Velocity", "Energy"], 2,
                 "Velocity has direction."),
                ("The unit of power is…", ["Joule", "Watt", "Newton", "Tesla"], 1, "1 W = 1 J/s.")],
    "chemistry": [("The pH of pure water at 25 °C is…", ["5", "7", "9", "14"], 1, "Neutral."),
                  ("Which is a noble gas?", ["Oxygen", "Nitrogen", "Argon", "Chlorine"], 2, "Group 18."),
                  ("The formula of common salt is…", ["NaCl", "KCl", "NaOH", "HCl"], 0, "Sodium chloride."),
                  ("Avogadro's number is about…", ["6.022 × 10²³", "3.0 × 10⁸", "9.8", "1.6 × 10⁻¹⁹"], 0,
                   "Particles per mole."),
                  ("Which bond shares electrons?", ["Ionic", "Covalent", "Metallic", "Hydrogen"], 1,
                   "Covalent bonds share electron pairs.")],
    "cs": [("Which data structure is FIFO?", ["Stack", "Queue", "Tree", "Graph"], 1, "First in, first out."),
           ("In Python, len([1, 2, 3]) returns…", ["2", "3", "4", "Error"], 1, "Three items."),
           ("Binary 1010 in decimal is…", ["8", "10", "12", "5"], 1, "8 + 2 = 10."),
           ("Which is NOT a programming language?", ["Python", "Java", "HTML", "C++"], 2,
            "HTML is a markup language."),
           ("Binary search runs in…", ["O(n)", "O(log n)", "O(n log n)", "O(1)"], 1, "It halves the range.")],
}
HEALTH = [("Asthma", "medium", "Carries an inhaler; avoid dusty rooms.", "Salbutamol inhaler"),
          ("Peanut allergy", "high", "Severe - no peanuts in class activities.", "Adrenaline auto-injector"),
          ("Type 1 diabetes", "high", "May need a snack break; inform the parent if unwell.", "Insulin"),
          ("Migraine", "low", "Sit away from bright windows.", None)]
SPECIAL_NEEDS = ["Dyslexia - give extra time and read questions aloud.",
                 "ADHD - short breaks help; seat near the front."]
CHAT = [("teacher", "Welcome to {batch}! Please bring your notebooks for every class. 📚"),
        ("student", "Will next week's test include the chapter on {topic}?"),
        ("teacher", "Yes - revise {topic} and the worksheet. You can ask the AI tutor any doubts. 🙂"),
        ("student", "Thank you! The notes from today's class were really helpful."),
        ("student", "Can we have a doubt-clearing session before the test?"),
        ("teacher", "Sure, we'll take the first 15 minutes of Friday's class for doubts.")]
ENQUIRIES = [("Ritu Malhotra", "Do you have weekend batches for Class 9 Mathematics? My son is preparing for "
                               "the Olympiad."),
             ("Sameer Qureshi", "What are the fees for Class 12 Physics and Chemistry together? Is there a "
                                "sibling discount?"),
             ("Anita Joshi", "Can my daughter attend a free trial class for Class 10 Science this week?")]


def _png(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, quality=88) if fmt == "JPEG" else img.save(buf, fmt)
    return buf.getvalue()


def avatar(name: str, seed: int) -> bytes:
    """A friendly placeholder portrait: soft gradient, initials. (Real photos are uploaded by the office.)"""
    rnd = random.Random(seed)
    hue = rnd.random()
    top = tuple(int(c * 255) for c in colorsys.hls_to_rgb(hue, 0.62, 0.55))
    bottom = tuple(int(c * 255) for c in colorsys.hls_to_rgb((hue + 0.08) % 1, 0.42, 0.6))
    size = 600
    img = Image.new("RGBA", (size, size), top + (255,))
    draw = ImageDraw.Draw(img)
    for y in range(size):  # vertical gradient
        t = y / size
        draw.line([(0, y), (size, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    o = ImageDraw.Draw(overlay)
    o.ellipse([150, 95, 450, 395], fill=(255, 255, 255, 70))   # head
    o.ellipse([70, 400, 530, 860], fill=(255, 255, 255, 70))   # shoulders
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    initials = "".join(p[0] for p in name.replace("Dr. ", "").split()[:2]).upper()
    f = font("bold", 120)
    box = draw.textbbox((0, 0), initials, font=f)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], 245 - (box[3] - box[1]) / 2 - box[1]), initials, font=f,
              fill=(255, 255, 255, 255))
    return _png(img.convert("RGB"), "JPEG")


def demo_id_proof(name: str, number: str) -> bytes:
    img = Image.new("RGB", (900, 560), (241, 245, 249))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 900, 90], fill=(100, 116, 139))
    d.text((36, 26), "DEMO ID PROOF", font=font("bold", 38), fill=(255, 255, 255))
    d.text((36, 150), name, font=font("bold", 40), fill=(15, 23, 42))
    d.text((36, 225), f"Document no.  {number}", font=font("regular", 30), fill=(51, 65, 85))
    d.text((36, 470), "Sample document generated for ClassNest demo data - not a real ID.",
           font=font("regular", 22), fill=(148, 163, 184))
    return _png(img)


def phone(rnd: random.Random) -> str:
    return rnd.choice("6789") + "".join(rnd.choice("0123456789") for _ in range(9))


def hhmm(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


async def demo_exists(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(User).where(User.created_by == DEMO_TAG)) or 0)


async def clear_demo(db: AsyncSession) -> dict[str, int]:
    """Remove every demo record and file (database cascades take care of the dependent rows)."""
    users = (await db.scalars(select(User).where(User.created_by == DEMO_TAG))).all()
    # a demo batch is marked in its description - which an admin can edit away - and always has demo lessons
    demo_lessons = select(Lesson.batch_id).where(Lesson.created_by == DEMO_TAG)
    batch_ids = list((await db.scalars(select(Batch.id).where(or_(Batch.description.like(f"{DEMO_MARK}%"),
                                                                  Batch.id.in_(demo_lessons))))).all())
    for u in users:
        for rel in (u.photo_path, u.id_proof_path, u.idcard_path):
            delete_file(rel)
    if batch_ids:  # the rows go with the batches; their video files must be removed explicitly
        for rel in (await db.scalars(select(Video.file_path).where(Video.batch_id.in_(batch_ids),
                                                                    Video.file_path.is_not(None)))).all():
            delete_file(rel)
        await db.execute(delete(Meeting).where(Meeting.batch_id.in_(batch_ids)))
        await db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))
    await db.execute(delete(User).where(User.created_by == DEMO_TAG))
    enquiries = await db.execute(delete(ContactMessage).where(ContactMessage.email.like(f"%@{DEMO_DOMAIN}")))
    await db.commit()
    return {"people": len(users), "batches": len(batch_ids), "enquiries": enquiries.rowcount or 0}


async def create_demo(db: AsyncSession, *, students: int = 36, seed: int = 2026,
                      log=print) -> dict[str, int]:
    rnd = random.Random(seed)
    today = dt.datetime.now(settings.tz).date()
    pw_hash = hash_password(DEMO_PASSWORD)  # one hash for every demo account
    counts = {"staff": 0, "students": 0, "batches": 0, "lessons": 0, "attendance": 0, "note chunks": 0, "fees": 0,
              "tasks": 0, "submissions": 0, "tests": 0, "attempts": 0, "video comments": 0}
    used_names: set[str] = set()

    async def new_user(full_name: str, role: str, gender: str, **fields) -> User:
        username = await unique_username(db, full_name.replace("Dr. ", ""))
        user = User(role=role, username=username, password_hash=pw_hash, full_name=full_name,
                    email=f"{username}@{DEMO_DOMAIN}", phone=phone(rnd), gender=gender,
                    address=rnd.choice(CITIES), id_proof_type="School ID" if role == "student" else "Aadhaar Card",
                    id_proof_number=f"DEMO-{rnd.randint(100000, 999999)}", created_by=DEMO_TAG,
                    photo_path=save_bytes(avatar(full_name, rnd.randint(0, 10**9)), "photos", ".jpg"),
                    id_proof_path="", **fields)
        user.id_proof_path = save_bytes(demo_id_proof(full_name, user.id_proof_number), "idproofs", ".png")
        db.add(user)
        await db.flush()
        user.id_code = make_id_code(user)
        return user

    # ---------------- staff
    log("Creating staff...")
    teachers: dict[str, User] = {}
    staff_users: list[User] = []
    for name, staff_role, qual, salary, gender, teaches in STAFF:
        joined = today - dt.timedelta(days=rnd.randint(120, 2400))
        user = await new_user(name, "staff", gender, staff_role=staff_role, qualification=qual or None,
                              designation=None, monthly_salary=Decimal(salary), joining_date=joined,
                              dob=dt.date(rnd.randint(1968, 1996), rnd.randint(1, 12), rnd.randint(1, 28)),
                              bank_account_name=name.replace("Dr. ", ""),
                              bank_account_number="".join(rnd.choice("0123456789") for _ in range(12)),
                              bank_ifsc=rnd.choice(["HDFC0001234", "ICIC0004321", "SBIN0001122", "KKBK0000958"]))
        staff_users.append(user)
        if teaches:
            teachers[teaches] = user
        counts["staff"] += 1

    # ---------------- batches (validated like the admin form)
    log("Creating batches...")
    batches: list[tuple[Batch, str]] = []
    for name, level, key, days, start, end, fee, color in BATCHES:
        existing = await db.scalar(select(Batch).where(func.lower(Batch.name) == name.lower()))
        if existing is not None:
            log(f"  - skipped {name}: a batch with this name exists")
            continue
        errors = await validate_batch(db, days=days, start=hhmm(start), end=hhmm(end), is_active=True)
        if errors:
            log(f"  - skipped {name}: {' '.join(errors)}")
            continue
        batch = Batch(name=name, class_level=level, subject=SUBJECTS[key], color=color, days=days,
                      start_time=hhmm(start), end_time=hhmm(end), monthly_fee=Decimal(fee), is_active=True,
                      teacher_id=teachers[key].id, description=f"{DEMO_MARK} {SUBJECTS[key]} for {level} - "
                                                               f"concepts, practice and weekly tests.")
        db.add(batch)
        await db.flush()
        batches.append((batch, key))
        counts["batches"] += 1
    by_level: dict[str, list[tuple[Batch, str]]] = {}
    for batch, key in batches:
        by_level.setdefault(batch.class_level, []).append((batch, key))

    # ---------------- students (+ enrollments, health, special needs)
    log(f"Creating {students} students...")
    levels = sorted(by_level) or ["Class 10"]
    student_users: list[tuple[User, list[tuple[Batch, str]], float]] = []
    for i in range(students):
        level = levels[i % len(levels)]
        while True:
            gender = rnd.choice(["Male", "Female"])
            first = rnd.choice(BOYS if gender == "Male" else GIRLS)
            surname = rnd.choice(SURNAMES)
            if f"{first} {surname}" not in used_names:
                used_names.add(f"{first} {surname}")
                break
        grade = int(level.split()[-1]) if level.split()[-1].isdigit() else 10
        dob = dt.date(today.year - grade - 6 + rnd.choice([0, 0, -1]), rnd.randint(1, 12), rnd.randint(1, 28))
        parent_female = rnd.random() < 0.5
        user = await new_user(
            f"{first} {surname}", "student", gender, dob=dob, class_level=level, school_name=rnd.choice(SCHOOLS),
            parent_name=f"{rnd.choice(PARENTS_F if parent_female else PARENTS_M)} {surname}",
            parent_relation="Mother" if parent_female else "Father",
            parent_email=None, parent_phone=None)
        user.parent_email = f"parent.{user.username}@{DEMO_DOMAIN}"
        user.parent_phone = phone(rnd)
        if i % 9 == 4:
            user.special_needs, user.special_needs_notes = True, SPECIAL_NEEDS[(i // 9) % len(SPECIAL_NEEDS)]
        if i % 7 == 3:
            cond, sev, notes, med = HEALTH[(i // 7) % len(HEALTH)]
            db.add(HealthRecord(user_id=user.id, condition=cond, severity=sev, notes=notes, medication=med,
                                emergency_contact=f"{user.parent_name} · {user.parent_phone}", added_by=DEMO_TAG))
        mine = by_level.get(level, [])
        for batch, _ in mine:
            db.add(Enrollment(user_id=user.id, batch_id=batch.id))
        student_users.append((user, mine, rnd.uniform(0.45, 0.97)))  # ability drives scores & attendance
        counts["students"] += 1
    await db.flush()

    log("Rendering ID cards...")
    for user in staff_users:
        await issue_id_card(user, [])
    for user, mine, _ in student_users:
        await issue_id_card(user, [b.name for b, _ in mine])

    # ---------------- fees (two months) and last month's salaries
    log("Fees and salaries...")
    this_month = today.replace(day=1)
    last_month = (this_month - dt.timedelta(days=1)).replace(day=1)
    receipt = 0
    for user, mine, _ability in student_users:
        for batch, _ in mine:
            for month in (last_month, this_month):
                period, due = month.strftime("%Y-%m"), month.replace(day=10)
                roll = rnd.random()
                if month == last_month:
                    status = "paid" if roll < 0.88 else "overdue"
                else:
                    status = "paid" if roll < 0.45 else "pending"
                fee = FeePayment(user_id=user.id, batch_id=batch.id, title="Tuition fee", period=period,
                                 amount=batch.monthly_fee, due_date=due, status=status, recorded_by=DEMO_TAG)
                if status == "paid":
                    receipt += 1
                    fee.paid_on = min(today, due - dt.timedelta(days=rnd.randint(0, 8)))
                    fee.method = rnd.choice(["UPI", "Cash", "Bank transfer", "UPI"])
                    fee.reference = f"UPI{rnd.randint(10**9, 10**10 - 1)}" if fee.method == "UPI" else None
                    fee.receipt_no = f"DEMO-{period.replace('-', '')}-{receipt:05d}"
                db.add(fee)
                counts["fees"] += 1
    for user in staff_users:
        db.add(SalaryPayment(user_id=user.id, title="Salary", period=last_month.strftime("%Y-%m"),
                             amount=user.monthly_salary, paid_on=last_month.replace(day=28), method="Bank transfer",
                             reference=f"NEFT{rnd.randint(10**8, 10**9 - 1)}", status="paid", recorded_by=DEMO_TAG))

    # ---------------- lessons: two past weeks (attendance + notes) and the next three days (live rooms)
    log("Classes and attendance...")
    holidays = await holidays_between(db, today - dt.timedelta(days=20), today + dt.timedelta(days=20))
    now = dt.datetime.now(dt.timezone.utc)
    for batch, key in batches:
        roster = [(u, a) for u, mine, a in student_users if any(b.id == batch.id for b, _ in mine)]
        topics = TOPICS[key]
        teacher = teachers[key]
        n = 0
        for offset in range(-14, 4):
            day = today + dt.timedelta(days=offset)
            if day.weekday() not in batch.days or classify(day, holidays).is_blocked:
                continue
            if offset == 0 and dt.datetime.now(settings.tz).time() > batch.start_time:
                continue  # don't create a class that "should" already be live
            topic = topics[n % len(topics)]
            n += 1
            past = offset < 0
            # warning/missed-class alerts are pre-marked as sent: demo classes shouldn't e-mail "no meeting yet" or
            # "class hasn't started" alarms (the 24-hour reminder for upcoming classes stays on)
            lesson = Lesson(batch_id=batch.id, title=topic, date=day, start_time=batch.start_time,
                            end_time=batch.end_time, status="completed" if past else "scheduled",
                            created_by=DEMO_TAG, warning_sent_at=now, missed_alert_sent_at=now,
                            reminder_sent_at=now if past else None)
            db.add(lesson)
            await db.flush()
            start = dt.datetime.combine(day, batch.start_time, tzinfo=settings.tz)
            meeting = Meeting(code=new_code(), kind="class", title=topic, batch_id=batch.id, lesson_id=lesson.id,
                              host_name=teacher.full_name, host_user_id=teacher.id, scheduled_start=start,
                              scheduled_minutes=batch.duration_minutes, status="ended" if past else "scheduled")
            if past:
                meeting.started_at = start
                meeting.ended_at = start + dt.timedelta(minutes=batch.duration_minutes - rnd.randint(0, 6))
                meeting.notes_status, meeting.notes_source = "ready", "fallback"
                summary = TOPIC_NOTES.get(topic, f"Today we covered the key ideas of {topic} with worked examples.")
                meeting.ai_notes = (f"## {topic}\n\n{summary}\n\n"
                                    f"**Homework** — complete the worksheet on {topic.lower()}.")
                for user, ability in roster:
                    present = rnd.random() < min(0.97, 0.55 + ability * 0.45)
                    db.add(Attendance(user_id=user.id, batch_id=batch.id, lesson_id=lesson.id, date=day,
                                      status="present" if present else "absent", source="meeting",
                                      detail=f"Attended {rnd.randint(91, 100)}% of the class" if present
                                      else "Did not join"))
                    counts["attendance"] += 1
            db.add(meeting)
            if past:  # index the notes like a real class does when it ends (meetings.py), for the AI tutor
                await db.flush()
                counts["note chunks"] += await index_document(
                    db, source_type="meeting", source_id=meeting.id, title=f"Class notes: {topic}",
                    content=summary, batch_id=batch.id, class_level=batch.class_level, subject=batch.subject)
            counts["lessons"] += 1

    # ---------------- homework, a published test, submissions and attempts
    log("Homework and tests...")

    async def open_day(day: dt.date) -> dt.date:
        for _ in range(10):
            if not classify(day, holidays).is_blocked:
                return day
            day += dt.timedelta(days=1)
        return day

    for batch, key in batches:
        roster = [(u, a) for u, mine, a in student_users if any(b.id == batch.id for b, _ in mine)]
        topic = TOPICS[key][0]
        past_task = Task(batch_id=batch.id, kind="homework", title=f"Worksheet: {topic}",
                         description=f"Solve all questions on {topic.lower()} in your notebook and upload a photo "
                                     "or type your answers.",
                         assign_date=await open_day(today - dt.timedelta(days=12)),
                         due_date=await open_day(today - dt.timedelta(days=5)), max_marks=Decimal(10),
                         created_by=DEMO_TAG)
        current_task = Task(batch_id=batch.id, kind="assignment", title=f"Practice set: {TOPICS[key][1]}",
                            description="Ten practice questions. Show your working.",
                            assign_date=await open_day(today - dt.timedelta(days=2)),
                            due_date=await open_day(today + dt.timedelta(days=3)), max_marks=Decimal(20),
                            created_by=DEMO_TAG)
        db.add_all([past_task, current_task])
        await db.flush()
        counts["tasks"] += 2
        for user, ability in roster:
            if rnd.random() < 0.55 + ability * 0.4:
                graded = rnd.random() < 0.8
                db.add(TaskSubmission(task_id=past_task.id, user_id=user.id, text="My answers are attached.",
                                      status="graded" if graded else "submitted", is_late=rnd.random() < 0.12,
                                      marks=Decimal(round(4 + ability * 6, 1)) if graded else None,
                                      feedback=rnd.choice(["Well done!", "Good effort - check question 4.",
                                                           "Neat work, keep it up."]) if graded else None,
                                      submitted_at=now - dt.timedelta(days=rnd.randint(5, 9)),
                                      graded_at=now - dt.timedelta(days=3) if graded else None))
                counts["submissions"] += 1
            if rnd.random() < 0.35:
                db.add(TaskSubmission(task_id=current_task.id, user_id=user.id, text="Done - see working below.",
                                      submitted_at=now - dt.timedelta(hours=rnd.randint(2, 40))))
                counts["submissions"] += 1

        bank = QUESTIONS[key]
        test = Test(batch_id=batch.id, title=f"Weekly test: {topic}", subject=batch.subject,
                    class_level=batch.class_level, topic=topic, difficulty="medium", duration_minutes=15,
                    instructions="Answer all questions. The timer starts when you begin.",
                    total_marks=Decimal(len(bank)), status="published", ai_generated=False,
                    generation_note="Demo test", created_by=DEMO_TAG)
        db.add(test)
        await db.flush()
        questions = []
        for pos, (text, options, answer, why) in enumerate(bank):
            q = Question(test_id=test.id, position=pos, kind="mcq", text=text, options=options, answer=str(answer),
                         explanation=why, marks=Decimal(1))
            db.add(q)
            questions.append(q)
        await db.flush()
        test_task = Task(batch_id=batch.id, kind="test", title=test.title,
                         assign_date=await open_day(today - dt.timedelta(days=7)),
                         due_date=await open_day(today - dt.timedelta(days=3)), test_id=test.id,
                         max_marks=test.total_marks, created_by=DEMO_TAG)
        db.add(test_task)
        await db.flush()
        counts["tests"] += 1
        counts["tasks"] += 1
        for user, ability in roster:
            if rnd.random() > 0.9:
                continue  # some students missed the test
            started = now - dt.timedelta(days=rnd.randint(3, 6), hours=rnd.randint(0, 5))
            per_q, score, answers = {}, 0, {}
            for q in questions:
                correct = rnd.random() < ability
                given = q.answer if correct else str((int(q.answer) + rnd.randint(1, 3)) % 4)
                answers[str(q.id)] = given
                per_q[str(q.id)] = {"given": given, "correct": correct, "awarded": 1 if correct else 0}
                score += 1 if correct else 0
            pct = score * 100 / len(questions)
            db.add(TestAttempt(test_id=test.id, user_id=user.id, task_id=test_task.id, started_at=started,
                               deadline_at=started + dt.timedelta(minutes=15),
                               submitted_at=started + dt.timedelta(minutes=rnd.randint(6, 14)), answers=answers,
                               score=Decimal(score), max_score=Decimal(len(questions)), status="graded",
                               per_question=per_q,
                               feedback="Outstanding work! 🌟" if pct >= 85 else
                               ("Good effort - review the questions you missed." if pct >= 50 else
                                "Let's go through this topic together - ask the AI tutor or your teacher.")))
            counts["attempts"] += 1

    # ---------------- a class video with comments and replies (a short clip shipped in app/data)
    clip = Path(__file__).resolve().parent.parent / "data" / "demo_lesson.webm"
    target = next(((b, k) for b, k in batches if b.name == "Class 10 · Mathematics"), None)
    if clip.exists() and target is not None:
        batch, key = target
        lesson = await db.scalar(select(Lesson).where(Lesson.batch_id == batch.id, Lesson.title == "Rational numbers",
                                                      Lesson.status == "completed").order_by(Lesson.date).limit(1))
        roster = [u for u, mine, _ in student_users if any(b.id == batch.id for b, _ in mine)]
        if lesson is not None and len(roster) >= 3:
            video = Video(batch_id=batch.id, title="Rational numbers — worked examples",
                          file_path=save_bytes(clip.read_bytes(), "videos", ".webm"), size_bytes=clip.stat().st_size,
                          mime="video/webm", duration_seconds=12, notes=TOPIC_NOTES["Rational numbers"],
                          uploaded_by=DEMO_TAG)
            db.add(video)
            await db.flush()
            lesson.video_id = video.id
            teacher, (first, second, third) = teachers[key], roster[:3]

            def comment(author: User, body: str, parent: VideoComment | None = None, at: int | None = None):
                row = VideoComment(video_id=video.id, parent_id=parent.id if parent else None, user_id=author.id,
                                   author_kind=author.role, author_name=author.full_name, body=body, video_seconds=at)
                db.add(row)
                counts["video comments"] += 1
                return row

            question = comment(first, "Why is 0.5 a rational number? 🤔", at=4)
            await db.flush()
            comment(teacher, "Because 0.5 = 1/2. Any number you can write as p/q with q ≠ 0 is rational.", question)
            comment(second, f"@{teacher.full_name.split()[0]} So every decimal that stops is rational too?", question)
            comment(teacher, f"@{second.full_name.split()[0]} Yes! 0.25 = 1/4 and 0.125 = 1/8. Repeating decimals "
                             f"like 0.333… are rational too.", question)
            comment(third, "Making the denominators equal (at 0:09) finally makes sense — thank you!", at=9)

    # ---------------- batch chat + website enquiries
    for batch, key in batches:
        roster = [u for u, mine, _ in student_users if any(b.id == batch.id for b, _ in mine)]
        teacher = teachers[key]
        for j, (who, text) in enumerate(CHAT):
            sender = teacher if who == "teacher" or not roster else roster[j % len(roster)]
            db.add(BatchChatMessage(batch_id=batch.id, user_id=sender.id, sender_name=sender.full_name,
                                    sender_role="Teacher" if sender is teacher else "Student",
                                    text=text.format(batch=batch.name, topic=TOPICS[key][1].lower())))
    for name, message in ENQUIRIES:
        db.add(ContactMessage(name=name, email=f"{name.split()[0].lower()}@{DEMO_DOMAIN}", phone=phone(rnd),
                              message=message, ip="127.0.0.1"))
    await db.commit()
    return counts
