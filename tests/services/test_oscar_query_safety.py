"""
The OSCAR records query must stay read-only: anything that could change data is refused.

This software is released under the AGPL-3.0 license.
"""
import pytest

from services import oscar_query


def test_a_plain_select_is_accepted_and_limited():
    sql = oscar_query.check_sql("SELECT demographic_no FROM demographic WHERE patient_status='AC'")
    assert sql.upper().startswith("SELECT") and "LIMIT" in sql.upper()


@pytest.mark.parametrize("sql", [
    "DELETE FROM demographic",
    "UPDATE demographic SET last_name='x'",
    "INSERT INTO demographic (last_name) VALUES ('x')",
    "DROP TABLE demographic",
    "SELECT 1; DELETE FROM demographic",
    "SELECT * FROM demographic INTO OUTFILE '/tmp/x'",
])
def test_anything_that_could_change_data_is_refused(sql):
    with pytest.raises(oscar_query.QueryError):
        oscar_query.check_sql(sql)


def test_the_queue_offers_an_approved_query_and_nothing_when_empty():
    queue = oscar_query.QueryQueue(lambda *args: None)
    assert queue.next() is None
    first = queue.add("SELECT 1")
    claimed = queue.next()
    assert claimed and claimed["id"] == first and claimed["sql"].upper().startswith("SELECT")
