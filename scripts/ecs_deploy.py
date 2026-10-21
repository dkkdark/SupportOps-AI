"""AWS CLI orchestration. No credentials or secret values are printed or persisted.

Called only by the explicitly dispatched staging workflow.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

FILE=Path('var/deployment.json')


def aws(*args):
    result=subprocess.run(['aws',*args,'--output','json'],check=True,text=True,capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def wait(*args):
    subprocess.run(['aws','ecs','wait',*args],check=True)


def main(command):
    cluster=os.environ['ECS_CLUSTER']
    service=os.environ['ECS_SERVICE']
    if command=='prepare':
        current=aws('ecs','describe-services','--cluster',cluster,'--services',service)['services'][0]
        old=current['taskDefinition']
        td=aws('ecs','describe-task-definition','--task-definition',old)['taskDefinition']
        allowed={'family','taskRoleArn','executionRoleArn','networkMode','containerDefinitions','volumes','placementConstraints','requiresCompatibilities','cpu','memory','runtimePlatform','ephemeralStorage','proxyConfiguration','pidMode','ipcMode'}
        td={k:v for k,v in td.items() if k in allowed}
        for container in td['containerDefinitions']:
            if container['name'] in ('app','orders'):
                container['image']=os.environ['IMAGE']
                if container['name']=='app':
                    container['environment']=[e for e in container.get('environment',[]) if e['name']!='CODE_VERSION']+[{'name':'CODE_VERSION','value':os.environ['CODE_VERSION']}]
        FILE.parent.mkdir(exist_ok=True)
        # Save rollback metadata before any deployment change.
        evidence={'previous':old,'image':os.environ['IMAGE'],'code':os.environ['CODE_VERSION'],'network':current['networkConfiguration'],'desired_count':max(current['desiredCount'],1)}
        FILE.write_text(json.dumps(evidence,indent=2))
        path=FILE.parent/'task-definition.json'
        path.write_text(json.dumps(td))
        new=aws('ecs','register-task-definition','--cli-input-json',f'file://{path}')['taskDefinition']['taskDefinitionArn']
        evidence['next']=new
        FILE.write_text(json.dumps(evidence,indent=2))
        print('Prepared immutable task definition and rollback metadata.')
    elif command=='rollout':
        state=json.loads(FILE.read_text())
        overrides={'containerOverrides':[{'name':'app','command':['alembic','upgrade','head']}]}
        overrides_path=FILE.parent/'migration-overrides.json'
        overrides_path.write_text(json.dumps(overrides))
        task=aws('ecs','run-task','--cluster',cluster,'--launch-type','FARGATE','--task-definition',state['next'],'--network-configuration',json.dumps(state['network']),'--overrides',f'file://{overrides_path}')
        if task.get('failures') or not task.get('tasks'):raise RuntimeError('Migration task could not start')
        arn=task['tasks'][0]['taskArn']
        wait('tasks-stopped','--cluster',cluster,'--tasks',arn)
        stopped=aws('ecs','describe-tasks','--cluster',cluster,'--tasks',arn)['tasks'][0]
        app=next(c for c in stopped['containers'] if c['name']=='app')
        if app.get('exitCode')!=0:raise RuntimeError('Migration failed; see CloudWatch task logs')
        aws('ecs','update-service','--cluster',cluster,'--service',service,'--task-definition',state['next'],'--desired-count',str(state['desired_count']))
        wait('services-stable','--cluster',cluster,'--services',service)
        deployed=aws('ecs','describe-services','--cluster',cluster,'--services',service)['services'][0]
        if deployed['taskDefinition']!=state['next']:raise RuntimeError('ECS rolled back deployment')
        print('Migration completed; ECS service stable on verified image.')
    elif command=='rollback':
        state=json.loads(FILE.read_text())
        aws('ecs','update-service','--cluster',cluster,'--service',service,'--task-definition',state['previous'])
        wait('services-stable','--cluster',cluster,'--services',service)
        print('Previous task definition restored. Database is not downgraded automatically.')
    else:raise SystemExit('Use prepare, rollout, or rollback')


if __name__=='__main__':main(sys.argv[1])
