import duckdb from 'duckdb';

const db = new duckdb.Database('data/warehouse/test.duckdb');
const conn = db.connect();

console.log('Creating test table...');
conn.run('CREATE TABLE IF NOT EXISTS test (id INTEGER, name VARCHAR)', (err) => {
  if (err) {
    console.error('Error creating table:', err);
    return;
  }
  
  console.log('Inserting data...');
  conn.run("INSERT INTO test VALUES (1, 'Alice'), (2, 'Bob')", (err) => {
    if (err) {
      console.error('Error inserting:', err);
      return;
    }
    
    console.log('Querying data...');
    conn.all('SELECT * FROM test', (err, res) => {
      if (err) {
        console.error('Error querying:', err);
        return;
      }
      
      console.log('Results:', res);
      
      conn.close();
      db.close();
    });
  });
});
