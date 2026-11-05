"""Encode source alias lists in the nested CSV format used by the matcher."""
import csv
import io


def encode_aliases(aliases):
    output = io.StringIO(newline='')
    csv.writer(output, delimiter=';', lineterminator='\r\n').writerow(aliases)
    return output.getvalue().removesuffix('\r\n')
