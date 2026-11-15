"""Provisional, post-acquisition judgments; NOT Gold or acquisition input.

Small traceable evidence sample, not an exhaustive annotation of all documents.
Quotes are locator anchors; full saved source context must be checked.
"""

REVIEW = [
    dict(id="BH-PV", arm="burggen-heuristic", suffix="2026-10-13__Mitteilungsblatt_f_web.pdf",
         segment=100, quote="Die Anlage wird von der Kegelabteilung des TSV Burggen\n errichtet und finanziert.",
         claim="Bürgerbrief berichtet Bau und Finanzierung der PV-Anlage am Dorfgemeinschaftshaus durch die TSV-Kegelabteilung.",
         tasks=[], kind="relevant_policy_statement", territory="Burggen",
         time="Ausgabe Oktober/November 2026; URL 2026-10-13; kein archivierter Stichtagsnachweis.",
         limitation="PV ist keine Wärmeversorgung; kein Beleg für die vier konkreten Aufgaben."),
    dict(id="BH-WIND", arm="burggen-heuristic", suffix="2026-10-13__Mitteilungsblatt_f_web.pdf",
         segment=200, quote="Vorranggebiete für die Windenergie festgelegt: WE 5, WE 6 und WE 7.",
         claim="Bürgerbrief berichtet regionale Windvorranggebiete auf Burggener Gebiet und Verbindlicherklärung vom 24.08.2026.",
         tasks=["B-T4"], kind="relevant_policy_statement", territory="Burggen / Region Oberland",
         time="Entscheidung 2026-08-24; Ausgabe Oktober/November 2026.",
         limitation="Regionale Entscheidung, kein kommunaler Klimazielbeschluss. S. 4 enthält Rückblick, aber keine zweite datierte Quelle."),
    dict(id="BS-HEAT", arm="burggen-structured", suffix="/gemeinde/gemeinderat",
         segment=21, quote="Betreiber Nahwäremenetz",
         claim="Profil von Thomas Kirchhofer nennt den Betrieb eines Nahwärmenetzes (Schreibweise der Quelle beibehalten).",
         tasks=["B-T3"], kind="task_candidate_ambiguous", territory="Netzgebiet unbekannt",
         time="Undatierte Seite; am 2026-10-20 erfasst.",
         limitation="Name in Segment 19; weder Betreiberorganisation noch Versorgungsgebiet oder kommunale Zuständigkeit belegt."),
    dict(id="AH-TARGET", arm="nuernberg-heuristic", suffix="/stadtportal/klimaplan.html",
         segment=1, quote="2025 hat der Stadtrat das Integrierte Klimaschutzkonzept (IKSK) beschlossen",
         claim="Stadtportal berichtet Stadtratsbeschluss 2025, 65 Prozent THG-Minderung bis 2030 und Klimaneutralität bis 2040.",
         tasks=["A-T1"], kind="task_support", territory="Nürnberg",
         time="Beschlussjahr 2025; genaue Datierung nicht angegeben; undatierter Web-Snapshot 2026-10-20.",
         limitation="Kommunale Selbstauskunft, keine Beschlussurkunde; Stichtagsversion und Vollständigkeit aller Ziele ungeprüft."),
    dict(id="AH-HEAT", arm="nuernberg-heuristic", suffix="/stadtportal/klimaplan.html",
         segment=8, quote="im Juni 2025 einen kommunalen Wärmeplan beschlossen",
         claim="Stadtportal berichtet Beschluss des Wärmeplans durch den Nürnberger Stadtrat im Juni 2025.",
         tasks=["A-T2"], kind="task_support", territory="Nürnberg",
         time="Beschlussmonat 2025-06; undatierter Web-Snapshot 2026-10-20.",
         limitation="Stadtrat ist Beschlussorgan, nicht gleich Planersteller oder Netzbetreiber. Spätere Änderungen ungeprüft."),
    dict(id="AH-PLANNER", arm="nuernberg-heuristic", suffix="2025_integriertes_klimaschutzkonzept_nuernberg.pdf",
         segment=3000, quote="kommunale Wärmeplanung der Stadt Nürnberg durch die IREES GmbH.",
         claim="IKSK vom 24.06.2025 übernimmt Ergebnisse der von IREES durchgeführten Nürnberger Wärmeplanung.",
         tasks=["A-T2"], kind="task_support", territory="Nürnberg",
         time="Berichtsdatum 2025-06-24 auf PDF-S. 1; Ergebnisse lagen für diesen Bericht vor.",
         limitation="Historischer Meilenstein, kein Nachweis unveränderten Status am Stichtag."),
    dict(id="AH-NETWORK", arm="nuernberg-heuristic", suffix="2025_integriertes_klimaschutzkonzept_nuernberg.pdf",
         segment=3101, quote="Fernwärme-\nnetz der N-ERGIE",
         claim="IKSK beschreibt N-ERGIE-Fernwärmenetz und Transformationsplan für Nürnberg; Stadtgebiet ist ausdrücklich benannt.",
         tasks=["A-T3"], kind="task_support", territory="Nürnberg",
         time="Berichtsdatum 2025-06-24, Wärmeabsatz-Basisjahr 2022, Ausbauplanung 2045.",
         limitation="N-ERGIE nicht mit N-ERGIE Netz GmbH gleichsetzen; Planungsziele sind keine realisierten Ausbauten."),
    dict(id="AS-GROUND", arm="nuernberg-structured", suffix="/aktuell_100494.html",
         segment=2, quote="Die Ergebnisse helfen beim Grundwasserschutz, bei der Stadt- und Infrastrukturplanung sowie bei der Wärmeplanung.",
         claim="Stadtportal beschreibt eine regionale Grundwassermesskampagne als Informationsgrundlage unter anderem für Wärmeplanung.",
         tasks=["A-T2"], kind="context_only", territory="Nürnberg und Erlangen/Fürth/Schwabach",
         time="19.–23. Oktober; verlinkter Flyertitel nennt 2026, Flyer nicht abgerufen.",
         limitation="Kein Status oder formaler Meilenstein des kommunalen Wärmeplans; nicht allein Nürnberg zuschreiben."),
]

# Readiness means observed documentary support, not exhaustive answers or Gold.
# Year/month precision is retained, never upgraded to a fictitious exact date.
TASK_REVIEW = {
    "nuernberg-heuristic": {
        "A-T1": ("snapshot_supported", ["AH-TARGET"], "Beschlussdatum nur Jahrespräzision; Vollständigkeit und Stichtagsversion offen."),
        "A-T2": ("snapshot_supported", ["AH-HEAT", "AH-PLANNER"], "Beschlussorgan und Planersteller getrennt; spätester Status nicht bewiesen."),
        "A-T3": ("snapshot_supported", ["AH-NETWORK"], "Belegte Netzrolle im IKSK, keine vollständige Akteurskartierung."),
        "A-T4": ("not_demonstrated_in_review", [], "Kein geprüftes Paar datierter Quellen zur selben Änderung; keine Abwesenheitsbehauptung."),
    },
    "nuernberg-structured": {
        "A-T1": ("not_demonstrated_in_review", [], "Kein Zielbeschluss im geprüften Evidenzsample."),
        "A-T2": ("context_only", ["AS-GROUND"], "Messkampagne belegt keinen Wärmeplanstatus."),
        "A-T3": ("not_demonstrated_in_review", [], "Kein geprüfter Wärmeversorgungsakteur."),
        "A-T4": ("not_demonstrated_in_review", [], "Kein geprüftes datiertes Quellenpaar."),
    },
    "burggen-heuristic": {
        "B-T1": ("not_demonstrated_in_review", [], "Regionale Windplanung ist kein Burggener Klimazielbeschluss."),
        "B-T2": ("not_demonstrated_in_review", [], "Keine geprüfte Wärmeplanstatusaussage."),
        "B-T3": ("not_demonstrated_in_review", [], "PV/Windrollen und Ausschussnamen belegen keine Wärmeversorgungszuständigkeit."),
        "B-T4": ("partial", ["BH-WIND"], "Nur eine rückblickende Quelle; zweite datierte Quelle fehlt."),
    },
    "burggen-structured": {
        "B-T1": ("not_demonstrated_in_review", [], "Kein geprüfter Zielbeschluss."),
        "B-T2": ("not_demonstrated_in_review", [], "Keine geprüfte Wärmeplanstatusaussage."),
        "B-T3": ("partial", ["BS-HEAT"], "Akteur/Berufsrolle genannt, Versorgungsgebiet und institutionelle Zuständigkeit offen."),
        "B-T4": ("not_demonstrated_in_review", [], "Kein geprüftes datiertes Quellenpaar."),
    },
}
