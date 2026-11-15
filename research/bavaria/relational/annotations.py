"""Pre-method original-first provisional relation and query annotations.

No reference here is an independent human judgment. Legal-entity aliases and
claim scope remain reviewable, and source-reported funding is not disbursement.
"""

ENTITIES = {
    "org:bmwk": ("Bundesministerium für Wirtschaft und Klimaschutz", "Organization"),
    "org:zug": ("Zukunft – Umwelt – Gesellschaft (ZUG) gGmbH", "Organization"),
    "org:ife_gmbh": ("Institut für Energietechnik IfE GmbH", "Organization"),
    "org:uez": ("ÜZ Mainfranken", "Organization"),
    "org:wust": ("Wust – Wind & Sonne GmbH & Co. KG", "Organization"),
    "org:ageen": (
        ("Arbeitsgemeinschaft Energieeffizienz- und Klimaschutz-Netzwerke Deutschland (AGEEN)"),
        "Organization",
    ),
    "org:sw_weiden": ("Kommunalunternehmen Stadtwerke Weiden i.d.OPf. AöR", "Organization"),
    "org:zeno": ("ZENO-ZukunftsEnergieNordoberpfalz GmbH", "Organization"),
    "org:etz": ("Energie-Technologisches Zentrum Nordoberpfalz GmbH", "Organization"),
    "org:ife_oth": ("Institut für Energietechnik der OTH Amberg-Weiden", "Organization"),
    "org:stmwi_historical": (
        "Bayerisches Staatsministerium für Wirtschaft, Energie und Technologie",
        "Organization",
    ),
    "municipality:09574164": ("Gemeinde Winkelhaid", "Municipality"),
    "municipality:09574140": ("Markt Neuhaus a.d.Pegnitz", "Municipality"),
    "municipality:09677114": ("Stadt Arnstein", "Municipality"),
    "municipality:09678193": ("Markt Werneck", "Municipality"),
    "municipality:09678115": ("Gemeinde Bergrheinfeld", "Municipality"),
    "municipality:09678150": ("Gemeinde Kolitzheim", "Municipality"),
    "municipality:09363000": ("Stadt Weiden i.d.OPf.", "Municipality"),
    "municipality:09174115": ("Stadt Dachau", "Municipality"),
    "project:kwp_winkelhaid": ("Kommunale Wärmeplanung Winkelhaid", "StudyProject"),
    "project:kwp_neuhaus": ("Kommunale Wärmeplanung Neuhaus a.d.Pegnitz 67K29135", "StudyProject"),
    "project:led_eichenau": ("LED-Hallenbeleuchtung Budrio-Allee Eichenau 67K32651", "Measure"),
    "project:wind_cooperation": ("Windenergie-Kooperation Arnstein/Werneck", "PlannedProject"),
    "project:led_bergrheinfeld": (
        "LED-Straßenbeleuchtung Bergrheinfeld/Garstadt/Am Bahnhof",
        "Measure",
    ),
    "project:pv_bergrheinfeld": (
        "PV- und Batteriespeicher-Förderprogramm Bergrheinfeld",
        "FundingProgramme",
    ),
    "project:energy_study_taufkirchen": (
        "Energiepotenzialanalyse Kläranlage Taufkirchen (Vils) 67K16681",
        "StudyProject",
    ),
    "project:enp_hilpoltstein": ("Teil-Energienutzungsplan Hilpoltstein 2018–2019", "StudyProject"),
    "programme:nki": ("Nationale Klimaschutzinitiative", "FundingProgramme"),
    "programme:beg": ("Bundesförderung effiziente Gebäude", "FundingProgramme"),
    "network:uez_nordwest": (
        "Klimaschutz-Netzwerk Nord/West der ÜZ Mainfranken eG",
        "InstitutionalNetwork",
    ),
    "status:study_complete": (
        "Wärmeplan als Studie erstellt; keine Infrastrukturabnahme",
        "SourceReportedStatus",
    ),
    "status:implementation_unspecified": (
        "Konkrete Umsetzung künftiger Maßnahmen noch nicht angegeben",
        "EpistemicStatus",
    ),
    "status:wind_count_unknown": (
        "Standorte und Anzahl der Windenergieanlagen stehen noch nicht fest",
        "EpistemicStatus",
    ),
    "status:no_local_building_energy_grant": (
        "Quelle berichtet derzeit keine städtischen Gebäude-/Energieförderprogramme",
        "NegativeSourceReport",
    ),
    "sector:regional_energy": (
        "Regionaler Energiedienstleister; Strom/Gas/Wasser/Abwasser/regenerative Energien",
        "InstitutionalRole",
    ),
    "time:2026-12-31": ("31.12.2026", "Date"),
    "time:2023-12-31": ("31.12.2023", "Date"),
    "time:2021-07-01": ("01.07.2021", "Date"),
    "time:2018-2019": ("September 2018 bis Dezember 2019", "Interval"),
    "amount:10000_eur_year": ("10.000 EUR pro Haushaltsjahr", "ProgrammeBudget"),
    "amount:85500_eur_estimate": ("85.500 EUR veranschlagte Maßnahmenkosten", "BudgetEstimate"),
    "amount:70_percent": ("70 Prozent Förderung", "FundingRate"),
}

# id, subject, predicate, object, unit prefix, excerpt (None = whole unit),
# explicit claim scope, modality, reference_time, context-unit prefixes.
RELATIONS = [
    (
        "W01",
        "org:bmwk",
        "FUNDS",
        "project:kwp_winkelhaid",
        "48e26f52fb7",
        None,
        ["09574164"],
        "SOURCE_REPORTED_FUNDING_NO_DISBURSEMENT",
        None,
        ["014a3e59b5b"],
    ),
    (
        "W02",
        "org:zug",
        "PROJECT_CARRIER_FOR",
        "project:kwp_winkelhaid",
        "48e26f52fb7",
        None,
        ["09574164"],
        "SOURCE_REPORTED_INSTITUTIONAL_ROLE",
        None,
        ["014a3e59b5b"],
    ),
    (
        "W03",
        "org:ife_gmbh",
        "PARTICIPATES_IN",
        "project:kwp_winkelhaid",
        "48e26f52fb7",
        None,
        ["09574164"],
        "SOURCE_REPORTED_INSTITUTIONAL_ROLE",
        None,
        ["014a3e59b5b"],
    ),
    (
        "W04",
        "municipality:09574164",
        "CONDUCTS_STUDY",
        "project:kwp_winkelhaid",
        "014a3e59b5b",
        None,
        ["09574164"],
        "SOURCE_REPORTED_STUDY",
        None,
        [],
    ),
    (
        "W05",
        "project:kwp_winkelhaid",
        "HAS_REPORTED_STATUS",
        "status:study_complete",
        "7c4db78382f",
        None,
        ["09574164"],
        "SOURCE_REPORTED_STUDY_COMPLETION",
        "September 2024 bis September 2025",
        [],
    ),
    (
        "N01",
        "org:bmwk",
        "FUNDS",
        "project:kwp_neuhaus",
        "5431dbcd5f9",
        None,
        ["09574140"],
        "SOURCE_REPORTED_FUNDING_NO_DISBURSEMENT",
        "01.11.2024–31.12.2026",
        [],
    ),
    (
        "N02",
        "org:zug",
        "PROJECT_CARRIER_FOR",
        "project:kwp_neuhaus",
        "5431dbcd5f9",
        None,
        ["09574140"],
        "SOURCE_REPORTED_INSTITUTIONAL_ROLE",
        None,
        [],
    ),
    (
        "N03",
        "project:kwp_neuhaus",
        "FUNDING_EXTENDED_UNTIL",
        "time:2026-12-31",
        "5431dbcd5f9",
        None,
        ["09574140"],
        "SOURCE_REPORTED_FUNDING_EXTENSION",
        "31.12.2026",
        [],
    ),
    (
        "N04",
        "project:kwp_neuhaus",
        "HAS_EVIDENCE_LIMIT",
        "status:implementation_unspecified",
        "a29556ebf1a",
        None,
        ["09574140"],
        "SOURCE_REPORTED_FUTURE_IMPLEMENTATION_UNSPECIFIED",
        None,
        [],
    ),
    (
        "E01",
        "org:bmwk",
        "FUNDS",
        "project:led_eichenau",
        "e54bea7c839",
        None,
        ["09179118"],
        "SOURCE_REPORTED_FUNDING_NO_DISBURSEMENT",
        None,
        [],
    ),
    (
        "E02",
        "project:led_eichenau",
        "HAS_ESTIMATED_COST",
        "amount:85500_eur_estimate",
        "e54bea7c839",
        None,
        ["09179118"],
        "BUDGET_ESTIMATE_NOT_GRANT",
        None,
        [],
    ),
    (
        "A01",
        "municipality:09677114",
        "PARTICIPATES_IN",
        "project:wind_cooperation",
        "49022f44c3c",
        None,
        ["09677114", "09678193"],
        "SOURCE_REPORTED_COOPERATION_AGREEMENT",
        "11.02.2025",
        [],
    ),
    (
        "A02",
        "municipality:09678193",
        "PARTICIPATES_IN",
        "project:wind_cooperation",
        "49022f44c3c",
        None,
        ["09677114", "09678193"],
        "SOURCE_REPORTED_COOPERATION_AGREEMENT",
        "11.02.2025",
        [],
    ),
    (
        "A03",
        "org:wust",
        "DEVELOPS",
        "project:wind_cooperation",
        "f680a065c37",
        None,
        ["09677114", "09678193"],
        "CONTRACTUAL_DEVELOPMENT_UNTIL_READY_TO_BUILD",
        None,
        ["49022f44c3c"],
    ),
    (
        "A04",
        "org:uez",
        "PLANS_GRID_CONNECTION_SUPPORT",
        "project:wind_cooperation",
        "c166e5acbcf",
        None,
        ["09677114", "09678193"],
        "PLANNED_NOT_OBSERVED_OPERATION",
        None,
        ["49022f44c3c"],
    ),
    (
        "A05",
        "project:wind_cooperation",
        "HAS_EVIDENCE_LIMIT",
        "status:wind_count_unknown",
        "f680a065c37",
        None,
        ["09677114", "09678193"],
        "EXPLICIT_UNKNOWN_NOT_ZERO",
        None,
        ["49022f44c3c"],
    ),
    (
        "B01",
        "org:uez",
        "CONTRACTED_FOR",
        "project:led_bergrheinfeld",
        "81bf418b69c",
        None,
        ["09678115"],
        "SOURCE_REPORTED_CONTRACT_AWARD",
        "3. Quartal 2022",
        [],
    ),
    (
        "B02",
        "municipality:09678115",
        "OFFERS_PROGRAMME",
        "project:pv_bergrheinfeld",
        "eacea91e330",
        None,
        ["09678115"],
        "SOURCE_REPORTED_PROGRAMME_LAUNCH",
        "01.07.2021",
        [],
    ),
    (
        "B03",
        "project:pv_bergrheinfeld",
        "HAS_ANNUAL_BUDGET",
        "amount:10000_eur_year",
        "eacea91e330",
        None,
        ["09678115"],
        "PROGRAMME_BUDGET_NOT_PAYMENT",
        "pro Haushaltsjahr",
        [],
    ),
    (
        "B04",
        "project:pv_bergrheinfeld",
        "EXPIRED_ON",
        "time:2023-12-31",
        "dff4b0cb567",
        None,
        ["09678115"],
        "EXPLICIT_SOURCE_REPORTED_EXPIRY",
        "31.12.2023",
        ["eacea91e330"],
    ),
    (
        "K01",
        "municipality:09678150",
        "PARTICIPATES_IN",
        "network:uez_nordwest",
        "a3f0e9fe480",
        None,
        ["09678150"],
        "SOURCE_REPORTED_NETWORK_RECOGNITION",
        "2023 bis 2026",
        [],
    ),
    (
        "K02",
        "org:uez",
        "NAMED_NETWORK_ORGANIZATION",
        "network:uez_nordwest",
        "a3f0e9fe480",
        None,
        ["09678150"],
        "POSSESSIVE_NETWORK_NAME_NOT_LEGAL_MANDATE",
        "2023 bis 2026",
        [],
    ),
    (
        "Z01",
        "municipality:09363000",
        "NAMED_JOINT_COMPANY_PARTICIPANT",
        "org:etz",
        "7e86466ac47",
        None,
        ["09363000"],
        "SOURCE_REPORTED_JOINT_COMPANY_NO_OWNERSHIP_SHARE",
        None,
        [],
    ),
    (
        "Z02",
        "org:sw_weiden",
        "NAMED_JOINT_COMPANY_PARTICIPANT",
        "org:etz",
        "7e86466ac47",
        None,
        ["09363000"],
        "SOURCE_REPORTED_JOINT_COMPANY_NO_OWNERSHIP_SHARE",
        None,
        [],
    ),
    (
        "Z03",
        "org:zeno",
        "NAMED_JOINT_COMPANY_PARTICIPANT",
        "org:etz",
        "7e86466ac47",
        None,
        ["09363000"],
        "SOURCE_REPORTED_JOINT_COMPANY_NO_OWNERSHIP_SHARE",
        None,
        [],
    ),
    (
        "Z04",
        "org:sw_weiden",
        "HAS_REPORTED_ROLE",
        "sector:regional_energy",
        "89e51ba3cea",
        None,
        ["09363000"],
        "SOURCE_REPORTED_INSTITUTIONAL_MISSION_NOT_EXCLUSIVE_RIGHT",
        None,
        [],
    ),
    (
        "T01",
        "programme:nki",
        "FUNDS",
        "project:energy_study_taufkirchen",
        "d626da94c6b",
        None,
        ["09177139"],
        "SOURCE_REPORTED_FUNDING_NO_DISBURSEMENT",
        None,
        ["73f727a221a"],
    ),
    (
        "T02",
        "org:zug",
        "PROJECT_CARRIER_FOR",
        "project:energy_study_taufkirchen",
        "0994cf404e2",
        None,
        ["09177139"],
        "SOURCE_REPORTED_PROJECT_CARE",
        None,
        ["73f727a221a"],
    ),
    (
        "H01",
        "org:ife_oth",
        "AUTHORS",
        "project:enp_hilpoltstein",
        "dc208ae3275",
        None,
        ["09576127"],
        "SOURCE_REPORTED_STUDY_AUTHORS",
        None,
        [],
    ),
    (
        "H02",
        "org:stmwi_historical",
        "FUNDS",
        "project:enp_hilpoltstein",
        "dc208ae3275",
        None,
        ["09576127"],
        "SOURCE_REPORTED_FUNDING_NO_DISBURSEMENT",
        None,
        [],
    ),
    (
        "H03",
        "project:enp_hilpoltstein",
        "HAS_FUNDING_RATE",
        "amount:70_percent",
        "dc208ae3275",
        None,
        ["09576127"],
        "SOURCE_REPORTED_RATE_NOT_PAYMENT",
        None,
        [],
    ),
    (
        "H04",
        "project:enp_hilpoltstein",
        "STUDY_DURING",
        "time:2018-2019",
        "f881985c9a7",
        None,
        ["09576127"],
        "SOURCE_REPORTED_STUDY_PERIOD",
        "September 2018 bis Dezember 2019",
        [],
    ),
    (
        "D01",
        "municipality:09174115",
        "REPORTS_NO_LOCAL_PROGRAMME",
        "status:no_local_building_energy_grant",
        "ec64fb3d33c",
        None,
        ["09174115"],
        "EXPLICIT_SCOPED_NEGATION_AT_UNDATED_SOURCE_TIME",
        None,
        [],
    ),
    (
        "D02",
        "programme:beg",
        "NAMED_EXTERNAL_FUNDING_OPTION_FOR",
        "municipality:09174115",
        "ec64fb3d33c",
        None,
        ["09174115"],
        "SOURCE_REPORTED_EXTERNAL_OPTION_NOT_MUNICIPAL_PROGRAMME",
        None,
        [],
    ),
]

# Structured graph patterns are supplied explicitly to ALL arms. This comparison
# isolates evidence representation/retrieval, not natural-language query planning.
TASKS = [
    (
        "Q01",
        "shared_funder",
        (
            "Welche Institution wird sowohl für die Wärmeplanung Winkelhaid als "
            "auch Neuhaus/Pegnitz als Fördergeber genannt?"
        ),
        [("?actor", "FUNDS", "project:kwp_winkelhaid"), ("?actor", "FUNDS", "project:kwp_neuhaus")],
        ["W01", "N01"],
    ),
    (
        "Q02",
        "cross_document_role",
        (
            "Welche Organisation betreut sowohl die Wärmeplanung Winkelhaid als "
            "auch die Energiepotenzialanalyse der Kläranlage Taufkirchen?"
        ),
        [
            ("?actor", "PROJECT_CARRIER_FOR", "project:kwp_winkelhaid"),
            ("?actor", "PROJECT_CARRIER_FOR", "project:energy_study_taufkirchen"),
        ],
        ["W02", "T02"],
    ),
    (
        "Q03",
        "temporal",
        (
            "Bis wann wurde die Förderphase der kommunalen Wärmeplanung "
            "Neuhaus/Pegnitz verlängert und belegt dies schon gebaute Wärmenetze?"
        ),
        [
            ("project:kwp_neuhaus", "FUNDING_EXTENDED_UNTIL", "?date"),
            ("project:kwp_neuhaus", "HAS_EVIDENCE_LIMIT", "?limit"),
        ],
        ["N03", "N04"],
    ),
    (
        "Q04",
        "role_distinction",
        (
            "Welche unterschiedlichen Rollen haben BMWK und ZUG bei der kommunalen "
            "Wärmeplanung Winkelhaid?"
        ),
        [
            ("org:bmwk", "FUNDS", "project:kwp_winkelhaid"),
            ("org:zug", "PROJECT_CARRIER_FOR", "project:kwp_winkelhaid"),
        ],
        ["W01", "W02"],
    ),
    (
        "Q05",
        "cross_document_temporal",
        (
            "Welche Institution war in Winkelhaid als Fördergeber beteiligt, und "
            "welchen Studienstatus berichtet der Abschlussbericht auf Seite 89?"
        ),
        [
            ("org:bmwk", "FUNDS", "project:kwp_winkelhaid"),
            ("project:kwp_winkelhaid", "HAS_REPORTED_STATUS", "?status"),
        ],
        ["W01", "W05"],
    ),
    (
        "Q06",
        "funding_vs_cost",
        (
            "Welche Förderungsaussage und welche veranschlagten Kosten nennt die "
            "Quelle für die LED-Hallenbeleuchtung in Eichenau?"
        ),
        [
            ("?actor", "FUNDS", "project:led_eichenau"),
            ("project:led_eichenau", "HAS_ESTIMATED_COST", "?amount"),
        ],
        ["E01", "E02"],
    ),
    (
        "Q07",
        "cross_document_roles",
        (
            "Welche Organisation ist als geplanter Netzpartner in Arnstein/Werneck "
            "und als beauftragter LED-Lieferant in Bergrheinfeld genannt?"
        ),
        [
            ("?actor", "PLANS_GRID_CONNECTION_SUPPORT", "project:wind_cooperation"),
            ("?actor", "CONTRACTED_FOR", "project:led_bergrheinfeld"),
        ],
        ["A04", "B01"],
    ),
    (
        "Q08",
        "municipality_scope",
        (
            "Welche beiden Kommunen sind ausdrücklich Parteien der "
            "Windenergie-Kooperation, obwohl die Quelle auf Arnsteins Website "
            "steht?"
        ),
        [("?municipality", "PARTICIPATES_IN", "project:wind_cooperation")],
        ["A01", "A02"],
    ),
    (
        "Q09",
        "multi_hop_actor",
        (
            "Welcher Projektentwickler übernimmt die Windentwicklung bis zur "
            "Baureife, und welcher Netzakteur soll die Einspeisung abstimmen?"
        ),
        [
            ("?developer", "DEVELOPS", "project:wind_cooperation"),
            ("?grid_actor", "PLANS_GRID_CONNECTION_SUPPORT", "project:wind_cooperation"),
        ],
        ["A03", "A04"],
    ),
    (
        "Q10",
        "explicit_unknown",
        (
            "Ist die Zahl der Windenergieanlagen Arnstein/Werneck in der Quelle "
            "festgelegt, und welche kommunale Kooperation gehört dazu?"
        ),
        [
            ("project:wind_cooperation", "HAS_EVIDENCE_LIMIT", "?unknown"),
            ("?municipality", "PARTICIPATES_IN", "project:wind_cooperation"),
        ],
        ["A05", "A01", "A02"],
    ),
    (
        "Q11",
        "network_project_bridge",
        (
            "Welche Organisation ist im Namen des Kolitzheimer "
            "Klimaschutz-Netzwerks genannt und soll zugleich die "
            "Wind-Netzanbindung Arnstein/Werneck unterstützen?"
        ),
        [
            ("?actor", "NAMED_NETWORK_ORGANIZATION", "network:uez_nordwest"),
            ("?actor", "PLANS_GRID_CONNECTION_SUPPORT", "project:wind_cooperation"),
        ],
        ["K02", "A04"],
    ),
    (
        "Q12",
        "programme_temporal",
        (
            "Welches jährliche Budget hatte das kommunale PV-/Speicherprogramm "
            "Bergrheinfeld, und wann lief es laut Quelle ab?"
        ),
        [
            ("project:pv_bergrheinfeld", "HAS_ANNUAL_BUDGET", "?budget"),
            ("project:pv_bergrheinfeld", "EXPIRED_ON", "?date"),
        ],
        ["B03", "B04"],
    ),
    (
        "Q13",
        "programme_provider",
        (
            "Welche Kommune legte das PV-/Batteriespeicher-Förderprogramm auf, und "
            "war dieses laut derselben Quelle später beendet?"
        ),
        [
            ("?municipality", "OFFERS_PROGRAMME", "project:pv_bergrheinfeld"),
            ("project:pv_bergrheinfeld", "EXPIRED_ON", "?date"),
        ],
        ["B02", "B04"],
    ),
    (
        "Q14",
        "company_cross_document",
        (
            "Welcher an der Energieagentur etz Nordoberpfalz beteiligte Akteur "
            "wird auf einer zweiten Seite als regionaler Energiedienstleister "
            "beschrieben?"
        ),
        [
            ("?actor", "NAMED_JOINT_COMPANY_PARTICIPANT", "org:etz"),
            ("?actor", "HAS_REPORTED_ROLE", "sector:regional_energy"),
        ],
        ["Z02", "Z04"],
    ),
    (
        "Q15",
        "company_participants",
        (
            "Welche drei Akteure werden als Beteiligte des gemeinsamen "
            "Unternehmens etz Nordoberpfalz genannt?"
        ),
        [("?actor", "NAMED_JOINT_COMPANY_PARTICIPANT", "org:etz")],
        ["Z01", "Z02", "Z03"],
    ),
    (
        "Q16",
        "study_funding",
        (
            "Wer erarbeitete den Teil-Energienutzungsplan Hilpoltstein, mit "
            "welchem Fördersatz und in welchem Zeitraum?"
        ),
        [
            ("?author", "AUTHORS", "project:enp_hilpoltstein"),
            ("project:enp_hilpoltstein", "HAS_FUNDING_RATE", "?rate"),
            ("project:enp_hilpoltstein", "STUDY_DURING", "?period"),
        ],
        ["H01", "H03", "H04"],
    ),
    (
        "Q17",
        "scoped_negation",
        (
            "Bietet die Stadt Dachau nach der archivierten Quelle ein eigenes "
            "Gebäude-/Energieförderprogramm an, oder werden externe Förderwege "
            "genannt?"
        ),
        [
            ("municipality:09174115", "REPORTS_NO_LOCAL_PROGRAMME", "?status"),
            ("?programme", "NAMED_EXTERNAL_FUNDING_OPTION_FOR", "municipality:09174115"),
        ],
        ["D01", "D02"],
    ),
    (
        "Q18",
        "false_premise",
        "Ist belegt, dass ZUG der LED-Lieferant in Bergrheinfeld ist?",
        [("org:zug", "CONTRACTED_FOR", "project:led_bergrheinfeld")],
        [],
        "ABSTAIN_NOT_ESTABLISHED",
    ),
    (
        "Q19",
        "false_premise",
        "Ist belegt, dass BMWK das Unternehmen etz Nordoberpfalz betreibt?",
        [("org:bmwk", "OPERATES", "org:etz")],
        [],
        "ABSTAIN_NOT_ESTABLISHED",
    ),
    (
        "Q20",
        "false_premise",
        (
            "Ist in der Quellenbasis eine abgeschlossene Windpark-Inbetriebnahme "
            "durch ÜZ Mainfranken in Arnstein/Werneck belegt?"
        ),
        [("org:uez", "HAS_VERIFIED_OPERATION", "project:wind_cooperation")],
        [],
        "ABSTAIN_NOT_ESTABLISHED",
    ),
]
