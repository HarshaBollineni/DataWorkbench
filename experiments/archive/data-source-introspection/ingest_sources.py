import sqlite3
import pandas as pd
from pathlib import Path
from tempfile import TemporaryDirectory


def introspect(path: str, kind: str) -> dict:
    """
    Introspect a data source and return table metadata.

    Args:
        path: Path to the data source file
        kind: Type of source ('sqlite', 'csv', or 'excel')

    Returns:
        dict: {table_name: {'columns': [str], 'datatypes': {col: str}, 'row_count': int}}

    Raises:
        ValueError: If kind is not in {'sqlite', 'csv', 'excel'}
    """
    if kind not in {'sqlite', 'csv', 'excel'}:
        raise ValueError(f"Unknown kind: {kind}. Must be 'sqlite', 'csv', or 'excel'.")

    result = {}

    if kind == 'sqlite':
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        # Get all user tables (exclude sqlite_* internal tables)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cursor.fetchall()]

        for table_name in tables:
            # Get column info via PRAGMA
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns_info = cursor.fetchall()
            columns = [col[1] for col in columns_info]
            datatypes = {col[1]: col[2] for col in columns_info}

            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]

            result[table_name] = {
                'columns': columns,
                'datatypes': datatypes,
                'row_count': row_count
            }

        conn.close()

    elif kind == 'csv':
        df = pd.read_csv(path)
        table_name = Path(path).stem

        columns = list(df.columns)
        datatypes = {col: str(df[col].dtype) for col in columns}
        row_count = len(df)

        result[table_name] = {
            'columns': columns,
            'datatypes': datatypes,
            'row_count': row_count
        }

    elif kind == 'excel':
        excel_file = pd.ExcelFile(path)
        sheet_names = excel_file.sheet_names

        for sheet_name in sheet_names:
            df = pd.read_excel(path, sheet_name=sheet_name)

            columns = list(df.columns)
            datatypes = {col: str(df[col].dtype) for col in columns}
            row_count = len(df)

            result[sheet_name] = {
                'columns': columns,
                'datatypes': datatypes,
                'row_count': row_count
            }

    return result


if __name__ == "__main__":
    try:
        with TemporaryDirectory() as tmpdir:
            # Test 1: SQLite
            db_path = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE test_table (id INTEGER, name TEXT)")
            cursor.execute("INSERT INTO test_table VALUES (1, 'Alice')")
            cursor.execute("INSERT INTO test_table VALUES (2, 'Bob')")
            cursor.execute("INSERT INTO test_table VALUES (3, 'Charlie')")
            conn.commit()
            conn.close()

            result = introspect(str(db_path), 'sqlite')
            assert 'test_table' in result
            assert len(result['test_table']['columns']) == 2
            assert result['test_table']['row_count'] == 3
            assert 'id' in result['test_table']['columns']
            assert 'name' in result['test_table']['columns']

            # Test 2: CSV
            csv_path = Path(tmpdir) / "test.csv"
            df = pd.DataFrame({
                'col1': [10, 20, 30],
                'col2': ['x', 'y', 'z']
            })
            df.to_csv(str(csv_path), index=False)

            result = introspect(str(csv_path), 'csv')
            assert 'test' in result
            assert len(result['test']['columns']) == 2
            assert result['test']['row_count'] == 3

            print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
