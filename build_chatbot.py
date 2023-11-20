import time
import json
import re
import sys
import boto3

ACCOUNT_ID = '297693275857'
REGION = 'us-east-1'
ENG_US = 'en_US'
VERSION_DRAFT = 'DRAFT'

session = boto3.Session(region_name=REGION)
client = session.client('lexv2-models')
iam = session.client('iam')
comprehend = session.client('comprehend')


def sleep(second, message='Wait for {second} seconds.'):
    print(message.format(second=second))
    time.sleep(second)


def create_bot(bot_name, role_name):
    try:
        print('Creating the bot...')
        response = client.create_bot(
            botName=bot_name,
            roleArn=f"arn:aws:iam::{ACCOUNT_ID}:role/aws-service-role/lexv2.amazonaws.com/{role_name}",
            dataPrivacy={'childDirected': False},
            idleSessionTTLInSeconds=300
        )
        while response['botStatus'] != 'Available':
            sleep(1)
            response = client.describe_bot(botId=response['botId'])
        return response['botId']
    except Exception as e:
        print(e)

    try:
        response = client.list_bots(filters=[{'name': 'BotName', 'values': [bot_name], 'operator': 'EQ'}])
        return response['botSummaries'][0]['botId']
    except Exception as e:
        print(e)


def create_bot_locale(bot_id, version=VERSION_DRAFT, locale_id=ENG_US, threshold=0.4):
    try:
        print('Creating the bot locale...')
        response = client.create_bot_locale(
            botId=bot_id,
            botVersion=version,
            localeId=locale_id,
            nluIntentConfidenceThreshold=threshold
        )
        while response['botLocaleStatus'] != 'NotBuilt':
            sleep(1)
            response = client.describe_bot_locale(botId=bot_id, botVersion=version, localeId=locale_id)
        return response
    except Exception as e:
        print(e)


def create_intent(name, bot_id, version=VERSION_DRAFT, locale=ENG_US):
    try:
        response = client.create_intent(intentName=name, botId=bot_id, botVersion=version, localeId=locale)
        return response['intentId']
    except Exception as e:
        print(e)

    try:
        response = client.list_intents(
            botId=bot_id, botVersion=version, localeId=locale,
            filters=[{'name': 'IntentName', 'values': [name], 'operator': 'EQ'}]
        )
        return response['intentSummaries'][0]['intentId']
    except Exception as e:
        print(e)


def update_intent(intent_id, intent_name, bot_id, question, answer, version=VERSION_DRAFT, locale=ENG_US):
    response = client.update_intent(
        intentId=intent_id,
        intentName=intent_name,
        botId=bot_id,
        botVersion=version,
        localeId=locale,
        sampleUtterances=[{"utterance": question}],
        dialogCodeHook={'enabled': False},
        fulfillmentCodeHook={
            'enabled': False,
            'postFulfillmentStatusSpecification': {
                'successResponse': {
                    'messageGroups': [{'message': {'plainTextMessage': {'value': answer}}}],
                    'allowInterrupt': True
                }
            }
        }
    )
    return response


def build_bot_locale(id_, version=VERSION_DRAFT, locale=ENG_US):
    response = client.build_bot_locale(botId=id_, botVersion=version, localeId=locale)
    while response['botLocaleStatus'] not in ['Built', 'Failed']:
        sleep(5, f"Waiting...{response['botLocaleStatus']}")
        response = client.describe_bot_locale(botId=id_, botVersion=version, localeId=locale)
    return response['botLocaleStatus']


def create_role(name):
    try:
        response = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps({
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Effect': 'Allow',
                        'Principal': {
                            'Service': 'lexv2.amazonaws.com'
                        },
                        'Action': 'sts:AssumeRole'
                    }
                ]
            })
        )
        print(f'Waiting for role {name} to be created.')
        waiter = iam.get_waiter('role_exists')
        waiter.wait(RoleName=name)
        print(f'Role {name} is created.')
        return response["Role"]
    except:
        print(f'Role {name} already exists.')


def attach_policy(name):
    iam.attach_role_policy(RoleName=name, PolicyArn='arn:aws:iam::aws:policy/AmazonLexFullAccess')
    print(f"Attach the policy AmazonLexFullAccess to the role {name}")


def generate_intent_name(text, style='pascal'):
    response = comprehend.detect_syntax(Text=text, LanguageCode='en')
    syntax = response['SyntaxTokens']
    tokens = [s['Text'] for i, s in enumerate(syntax) if s['PartOfSpeech']['Tag'] in
              ['ADV', 'ADJ', 'NOUN', 'VERB', 'PART', 'PRON', 'PROPN', 'NUM', 'ADP']
              or (i == 0 and s['PartOfSpeech']['Tag'] in ['AUX'])]

    if style == 'pascal':
        return ' '.join(tokens).title().replace(' ', '')

    if style == 'snake':
        return '_'.join(tokens).lower()

    return ''.join(tokens)


def read_artical(filename):
    with open(filename, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    if not lines:
        return

    story = {
        'BotName': None,
        'items': []
    }

    mode = None

    for line in lines:
        mb_ = re.match('^BOTNAME:(.*)', line)
        mq_ = re.match('^[Qq]:(.*)', line)
        ma_ = re.match('^[Aa]:(.*)', line)

        if mb_ and mb_.group(1):
            story['BotName'] = mb_.group(1).strip()
        elif mq_ and mq_.group(1):
            mode = 'q'
            item = {'q': mq_.group(1).strip()}
            story['items'].append(item)
        elif ma_ and ma_.group(1):
            mode = 'a'
            item['a'] = ma_.group(1).strip()
        elif mode == 'q':
            item['q'] += line
        elif mode == 'a':
            item['a'] += line

    return story


def creator(filename):
    story = read_artical(filename)

    bot_name = story['BotName']
    role_name = f'{bot_name}-role'

    create_role(role_name)
    attach_policy(role_name)
    bot_id = create_bot(bot_name, role_name)
    create_bot_locale(bot_id)

    for item in story['items']:
        question = item['q']
        answer = item['a'][:1000]
        intent_name = generate_intent_name(question)
        intent_id = create_intent(intent_name, bot_id)
        update_intent(intent_id, intent_name, bot_id, question, answer)

    sleep(2)

    print(f'Bot ID: {bot_id}')
    response = build_bot_locale(bot_id)
    print(response)

    print('Your bot are now ready. Click the link below to see more detail:\n'
          f'https://{REGION}.console.aws.amazon.com/lexv2/home?region={REGION}#bot/{bot_id}')

    return bot_id


if __name__ == '__main__':
    creator(sys.argv[1])
