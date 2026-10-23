// The default test pages the LAMP/LEMP creators generate. Byte parity
// with the python f-string templates (index.php in the docroot).
package creator

import "strings"

// lampIndexPHP — python create_lamp_site()'s index.php (PDO guestbook).
func lampIndexPHP(domain, dbName, dbUser, dbPass string) string {
	r := strings.NewReplacer(
		"__DOMAIN__", domain,
		"__DB__", dbName,
		"__USER__", dbUser,
		"__PASS__", dbPass,
	)
	return r.Replace(lampIndexTemplate)
}

const lampIndexTemplate = `<?php
$host = 'localhost';
$db   = '__DB__';
$user = '__USER__';
$pass = '__PASS__';

$error   = null;
$pdo     = null;
$entries = [];
$inserted = false;

try {
    $pdo = new PDO("mysql:host=$host;dbname=$db", $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    $pdo->exec("CREATE TABLE IF NOT EXISTS test_entries (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        name       VARCHAR(100) NOT NULL,
        message    VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )");

    if ($_SERVER['REQUEST_METHOD'] === 'POST'
        && !empty($_POST['name'])
        && !empty($_POST['message'])) {
        $stmt = $pdo->prepare(
            "INSERT INTO test_entries (name, message) VALUES (?, ?)"
        );
        $stmt->execute([
            htmlspecialchars($_POST['name']),
            htmlspecialchars($_POST['message'])
        ]);
        $inserted = true;
    }

    $entries = $pdo->query(
        "SELECT * FROM test_entries ORDER BY created_at DESC LIMIT 20"
    )->fetchAll(PDO::FETCH_ASSOC);

} catch (PDOException $e) {
    $error = $e->getMessage();
}
?><!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>__DOMAIN__</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: sans-serif; padding: 2rem; max-width: 750px; margin: auto; color: #212529; }
    h1 { margin-bottom: .25rem; }
    h3 { margin-top: 2rem; border-bottom: 1px solid #dee2e6; padding-bottom: .4rem; }
    .badge { display:inline-block; padding:2px 10px; border-radius:4px; font-size:.8rem; font-weight:600; }
    .green { background:#d1e7dd; color:#0a3622; }
    .red   { background:#f8d7da; color:#58151c; }
    .form-row { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin-top:.75rem; }
    input[type=text] { padding:6px 10px; border:1px solid #ced4da; border-radius:4px; width:200px; }
    button { padding:6px 16px; background:#0d6efd; color:#fff; border:none; border-radius:4px; cursor:pointer; }
    button:hover { background:#0b5ed7; }
    table { width:100%; border-collapse:collapse; margin-top:.75rem; font-size:.9rem; }
    th,td { padding:8px 12px; border:1px solid #dee2e6; text-align:left; }
    th { background:#f8f9fa; font-weight:600; }
    tr:hover td { background:#f8f9fa; }
    pre { background:#1a1a2e; color:#a8d8ea; padding:1rem; border-radius:6px;
           font-size:.82rem; overflow-x:auto; line-height:1.6; }
    .notice { background:#fff3cd; border:1px solid #ffc107; border-radius:4px;
               padding:.5rem 1rem; margin-top:.5rem; font-size:.85rem; }
    code { background:#e9ecef; padding:1px 5px; border-radius:3px; font-size:.9em; }
    .empty { color:#6c757d; font-style:italic; }
  </style>
</head>
<body>
  <h1>__DOMAIN__</h1>

<?php if ($error): ?>
  <p><span class="badge red">✗ DB Error</span> <code><?= htmlspecialchars($error) ?></code></p>
<?php else: ?>
  <p>
    <span class="badge green">✓ Connected</span>
    &nbsp; database <code>__DB__</code> &nbsp;·&nbsp; user <code>__USER__</code>
  </p>

  <?php if ($inserted): ?>
  <div class="notice">✓ Entry inserted successfully.</div>
  <?php endif; ?>

  <h3>Insert test data</h3>
  <form method="POST">
    <div class="form-row">
      <input type="text" name="name"    placeholder="Your name"    required maxlength="100">
      <input type="text" name="message" placeholder="Test message"  required maxlength="255">
      <button type="submit">Insert row</button>
    </div>
  </form>

  <h3>Rows in <code>test_entries</code></h3>
  <?php if ($entries): ?>
  <tr>
    <tr><th>ID</th><th>Name</th><th>Message</th><th>Created At</th></tr>
    <?php foreach ($entries as $row): ?>
    <tr>
      <td><?= $row['id'] ?></td>
      <td><?= $row['name'] ?></td>
      <td><?= $row['message'] ?></td>
      <td><?= $row['created_at'] ?></td>
    </tr>
    <?php endforeach; ?>
  </table>
  <?php else: ?>
  <p class="empty">No rows yet — submit the form above.</p>
  <?php endif; ?>

  <h3>Verify from your Linux server</h3>
  <pre># Connect to MariaDB
mysql

# Connect as the site DB user
mysql -u __USER__ -p'__PASS__' __DB__

# Show all test entries
mysql __DB__ -e "SELECT * FROM test_entries;"

# Count rows
mysql __DB__ -e "SELECT COUNT(*) AS total FROM test_entries;"

# Show tables in the database
mysql __DB__ -e "SHOW TABLES;"

# Show the table structure
mysql __DB__ -e "DESCRIBE test_entries;"

# Show all databases
mysql -e "SHOW DATABASES;"

# Delete all test entries (reset)
mysql __DB__ -e "DELETE FROM test_entries;"</pre>

<?php endif; ?>
</body>
</html>
`

// lempIndexPHP — python create_lemp_site()'s condensed index.php.
func lempIndexPHP(domain, dbName, dbUser, dbPass string) string {
	r := strings.NewReplacer(
		"__DOMAIN__", domain,
		"__DB__", dbName,
		"__USER__", dbUser,
		"__PASS__", dbPass,
	)
	return r.Replace(lempIndexTemplate)
}

const lempIndexTemplate = `<?php
$host = 'localhost';
$db   = '__DB__';
$user = '__USER__';
$pass = '__PASS__';
$error = null; $pdo = null; $entries = []; $inserted = false;
try {
    $pdo = new PDO("mysql:host=$host;dbname=$db", $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec("CREATE TABLE IF NOT EXISTS test_entries (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        message VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )");
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['name']) && !empty($_POST['message'])) {
        $stmt = $pdo->prepare("INSERT INTO test_entries (name, message) VALUES (?, ?)");
        $stmt->execute([htmlspecialchars($_POST['name']), htmlspecialchars($_POST['message'])]);
        $inserted = true;
    }
    $entries = $pdo->query("SELECT * FROM test_entries ORDER BY created_at DESC LIMIT 20")->fetchAll(PDO::FETCH_ASSOC);
} catch (PDOException $e) { $error = $e->getMessage(); }
?><!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>__DOMAIN__ — LEMP</title>
<style>
  body{font-family:sans-serif;padding:2rem;max-width:750px;margin:auto;color:#212529}
  .badge{display:inline-block;padding:2px 10px;border-radius:4px;font-size:.8rem;font-weight:600}
  .green{background:#d1e7dd;color:#0a3622}.red{background:#f8d7da;color:#58151c}
  table{width:100%;border-collapse:collapse;margin-top:.75rem;font-size:.9rem}
  th,td{padding:8px 12px;border:1px solid #dee2e6;text-align:left}th{background:#f8f9fa}
  input[type=text]{padding:6px 10px;border:1px solid #ced4da;border-radius:4px;width:200px}
  button{padding:6px 16px;background:#0d6efd;color:#fff;border:none;border-radius:4px;cursor:pointer}
  code{background:#e9ecef;padding:1px 5px;border-radius:3px}
</style></head><body>
<h1>__DOMAIN__</h1>
<?php if ($error): ?>
  <p><span class="badge red">✗ DB Error</span> <code><?= htmlspecialchars($error) ?></code></p>
<?php else: ?>
  <p><span class="badge green">✓ Connected</span> &nbsp; db <code>__DB__</code> · user <code>__USER__</code></p>
  <p>PHP <?= phpversion() ?> · Nginx · MariaDB</p>
  <?php if ($inserted): ?><p style="color:green">✓ Entry inserted.</p><?php endif; ?>
  <h3>Insert test entry</h3>
  <form method="POST">
    <input type="text" name="name" placeholder="Name" required>
    <input type="text" name="message" placeholder="Message" required>
    <button type="submit">Insert</button>
  </form>
  <h3>Entries</h3>
  <?php if ($entries): ?>
  <table><tr><th>ID</th><th>Name</th><th>Message</th><th>Created</th></tr>
  <?php foreach ($entries as $r): ?>
  <tr><td><?= $r['id'] ?></td><td><?= htmlspecialchars($r['name']) ?></td>
      <td><?= htmlspecialchars($r['message']) ?></td><td><?= $r['created_at'] ?></td></tr>
  <?php endforeach; ?></table>
  <?php else: ?><p style="color:#6c757d;font-style:italic">No entries yet.</p><?php endif; ?>

  <h3>Verify from your Linux server</h3>
  <pre># Connect to MariaDB
mysql

# Connect as the site DB user
mysql -u __USER__ -p'__PASS__' __DB__

# Show all test entries
mysql __DB__ -e "SELECT * FROM test_entries;"

# Count rows
mysql __DB__ -e "SELECT COUNT(*) AS total FROM test_entries;"

# Show tables in the database
mysql __DB__ -e "SHOW TABLES;"

# Show the table structure
mysql __DB__ -e "DESCRIBE test_entries;"

# Show all databases
mysql -e "SHOW DATABASES;"

# Delete all test entries (reset)
mysql __DB__ -e "DELETE FROM test_entries;"</pre>
<?php endif; ?>
</body></html>
`
