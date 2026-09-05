"""Guard generic clients as well as the preferred V2 publisher."""
import ast
from pathlib import Path
import boto3
import pytest
from botocore.stub import Stubber
from festival_bloomberg.lake.publication_guard import guard_s3_client, FACTOR_CURRENT

@pytest.fixture
def client():
    return guard_s3_client(boto3.client('s3', region_name='auto', aws_access_key_id='test', aws_secret_access_key='test'))

@pytest.mark.parametrize('operation,extra', [
    ('put_object', {'Body': b'{}'}), ('copy_object', {'CopySource':'bucket/object'}),
    ('delete_object', {}), ('create_multipart_upload', {}),
    ('complete_multipart_upload', {'UploadId':'test'}),
    ('put_object', {'Body':b'{}','IfMatch':'*'}),
])
def test_unsafe_dispatch_rejected(client, operation, extra):
    with Stubber(client) as stub:
        stub.add_response(operation, {})
        with pytest.raises(ValueError, match='REQUIRES_CONDITIONAL'):
            getattr(client,operation)(Bucket='lake', Key=FACTOR_CURRENT, **extra)

def test_bulk_delete_rejected(client):
    with Stubber(client) as stub:
        stub.add_response('delete_objects', {})
        with pytest.raises(ValueError, match='REQUIRES_CONDITIONAL'):
            client.delete_objects(Bucket='lake', Delete={'Objects':[{'Key':FACTOR_CURRENT}]})

@pytest.mark.parametrize('condition', [{'IfMatch':'"parent-etag"'}, {'IfNoneMatch':'*'}])
def test_conditional_publication_accepted(client, condition):
    with Stubber(client) as stub:
        stub.add_response('put_object', {'ETag':'"child"'})
        assert client.put_object(Bucket='lake',Key=FACTOR_CURRENT,Body=b'{}',**condition)['ETag']=='"child"'

def test_all_repository_s3_factories_guarded():
    root=Path(__file__).resolve().parents[2]
    found=[]
    for base in ('python','scripts'):
        for path in (root/base).rglob('*.py'):
            tree=ast.parse(path.read_text())
            parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'client','resource'} and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value=='s3':
                    found.append(str(path.relative_to(root)))
                    parent=parents.get(node)
                    assert isinstance(parent,ast.Call) and isinstance(parent.func,ast.Name) and parent.func.id=='guard_s3_client',str(path)
    assert len(found)==4
