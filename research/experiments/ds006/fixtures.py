"""Synthetic fixture oracle only. No claims about actual municipalities.

Development fixtures precede policy selection. Held-out cases vary wording,
layout, subject and negative-keyword/context failure modes; never tune to them.
"""


def fixture(name, html, needles, *, split="development", relevant=True,
            municipality="SYN-A", subjects=("SYN-A",), kind="evidence"):
    return dict(id=name, html=html, needles=needles, split=split, relevant=relevant,
                municipality=municipality, subjects=list(subjects), kind=kind,
                url=f"https://synthetic.example.test/{name}")


def fixtures():
    return [
        fixture("navigation", '<nav><a href="/ris">Ratsinformationssystem</a></nav>',
                ["Ratsinformationssystem"], kind="discovery"),
        fixture("target", '<h2>Klimaziel</h2><p>Der Rat beschloss am 12.06.2025 '
                'die Klimaneutralität bis 2040 für das gesamte Gebiet der Modellstadt Alpha.</p>',
                ["12.06.2025", "2040", "Alpha"]),
        fixture("short_heat", '<h2>Wärme</h2><p>Die Wärmeplanung wurde am 03.05.2025 beschlossen.</p>',
                ["Wärmeplanung", "03.05.2025"]),
        fixture("context", '<h2>Wärmeplanung</h2><p>Die Verwaltung legte den ersten Entwurf '
                'am 04.07.2025 öffentlich aus; die Beratung findet erst im folgenden Monat statt.</p>',
                ["Wärmeplanung", "04.07.2025", "Entwurf"]),
        fixture("emissions", '<h2>Bilanz</h2><table><tr><th>Jahr</th><th>Emissionen</th>'
                '<th>Einheit</th></tr><tr><td>2025</td><td>1.250</td><td>t CO2e</td></tr></table>',
                ["2025", "1.250", "t CO2e", "Emissionen"]),
        fixture("no_keyword_table", '<h2>Bestand</h2><table><tr><th>Jahr</th><th>Menge</th>'
                '<th>Einheit</th></tr><tr><td>2024</td><td>17</td><td>MWh</td></tr></table>',
                ["2024", "17", "MWh", "Menge"]),
        fixture("ambiguous_actor", '<p>Die Stadtwerke planen ein Wärmenetz ab 2027. '
                'Der Text benennt weder das Versorgungsgebiet noch eine verantwortliche Gemeinde.</p>',
                ["Stadtwerke", "2027", "weder"], subjects=()),
        fixture("foreign_correct", '<p>Die Modellgemeinde Beta beschloss am 11.02.2024 '
                'ein Klimaziel ausschließlich für ihr eigenes Gemeindegebiet, nicht für Alpha.</p>',
                ["Beta", "11.02.2024", "nicht für Alpha"], municipality="SYN-B", subjects=("SYN-B",)),
        fixture("foreign_seed_inheritance", '<p>Die Modellgemeinde Gamma beschloss am 09.03.2024 '
                'ein Klimaziel ausschließlich für Gamma; die Veröffentlichung liegt auf dem Portal von Alpha.</p>',
                ["Gamma", "09.03.2024"], subjects=("SYN-C",)),
        fixture("sports", '<p>Der Sportverein öffnet das Vereinsheim jeden Dienstag und '
                'Donnerstag für alle Mitglieder und Gäste der örtlichen Mannschaft.</p>',
                ["Sportverein", "Dienstag"], relevant=False),
        fixture("earlier", '<p>Das Klimaprogramm K-7 sah am 10.01.2020 eine Reduktion '
                'um 20 Prozent bis 2030 vor; diese Angabe gilt für die damalige Fassung.</p>',
                ["K-7", "10.01.2020", "20 Prozent"]),
        fixture("later", '<p>Das Klimaprogramm K-7 sieht nach dem Beschluss vom 10.01.2025 '
                'eine Reduktion um 40 Prozent bis 2030 vor und ersetzt die frühere Fassung.</p>',
                ["K-7", "10.01.2025", "40 Prozent"]),
        fixture("held_compact", '<h3>Radverkehr</h3><p>Der Ausbau beginnt am 01.08.2026.</p>',
                ["Radverkehr", "01.08.2026"], split="held_out"),
        fixture("held_units", '<table><caption>Verbrauch</caption><tr><th>Stand</th>'
                '<th>Volumen (m³)</th></tr><tr><td>2023</td><td>482</td></tr></table>',
                ["2023", "482", "m³", "Volumen"], split="held_out"),
        fixture("held_negative_keyword", '<p>Kontakt für die kommunale Wärmeplanung ist '
                'das Bauamt der Modellstadt Alpha; der Rat bestätigte diese Zuständigkeit am 02.02.2026.</p>',
                ["Bauamt", "02.02.2026", "Wärmeplanung"], split="held_out"),
        fixture("held_short_parser", '<h2>Wärme</h2><p>Start: 2028.</p>',
                ["Wärme", "Start: 2028"], split="held_out"),
        fixture("held_large_table", '<table><tr><th>Inventarjahr</th><th>Emissionen in t CO2e</th></tr>'
                + ''.join(f'<tr><td>{1900+i}</td><td>{10000+i}</td></tr>' for i in range(160))
                + '<tr><td>2061</td><td>987654</td></tr></table>',
                ["Inventarjahr", "t CO2e", "2061", "987654"], split="held_out"),
        fixture("held_noise", '<p>Am Wochenende findet ein öffentlicher Markt mit '
                'regionalem Brot und handgefertigten Waren auf dem zentralen Platz statt.</p>',
                ["Markt", "Brot"], split="held_out", relevant=False),
    ]
