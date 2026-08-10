import duckdb from 'duckdb';

const db = new duckdb.Database('data/warehouse/festival_bloomberg.duckdb');
const conn = db.connect();

console.log('=== Checking tables ===');
conn.all('SHOW TABLES', (err, res) => {
  if (err) {
    console.error('Error:', err);
  } else {
    console.log('Tables:', res);
  }
  
  console.log('\n=== core.artists count ===');
  conn.all('SELECT COUNT(*) as count FROM core.artists', (err, res) => {
    if (err) {
      console.error('Error:', err);
    } else {
      console.log('Count:', res[0]);
    }
    
    console.log('\n=== core.lineup_slots count ===');
    conn.all('SELECT COUNT(*) as count FROM core.lineup_slots', (err, res) => {
      if (err) {
        console.error('Error:', err);
      } else {
        console.log('Count:', res[0]);
      }
      
      console.log('\n=== raw.lineup_observations count ===');
      conn.all('SELECT COUNT(*) as count FROM raw.lineup_observations', (err, res) => {
        if (err) {
          console.error('Error:', err);
        } else {
          console.log('Count:', res[0]);
        }
        
        console.log('\n=== core.festivals count ===');
        conn.all('SELECT COUNT(*) as count FROM core.festivals', (err, res) => {
          if (err) {
            console.error('Error:', err);
          } else {
            console.log('Count:', res[0]);
          }
          
          console.log('\n=== core.festival_editions count ===');
          conn.all('SELECT COUNT(*) as count FROM core.festival_editions', (err, res) => {
            if (err) {
              console.error('Error:', err);
            } else {
              console.log('Count:', res[0]);
            }
            
            conn.close();
            db.close();
          });
        });
      });
    });
  });
});
