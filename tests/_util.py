"""Общие хелперы для тестов."""
import pandas as pd


def make_df(records):
    """DataFrame выписки из списка словарей с колонкой 'Дата' (строки → datetime)."""
    df = pd.DataFrame(records)
    df["Дата"] = pd.to_datetime(df["Дата"])
    return df
