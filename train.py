import time
from train import *
import wandb
from config import *
from model import *
from utilities import *

def train(model, optimizer, criterion, iterator):
    """
    params
    ---
    model : nn.Module
    optimizer : nn.Object
    criterion : nn.Object
    iterator : torch.utils.data.DataLoader

    returns
    ---
    epoch_loss / len(iterator) : float
        overall loss
    """
    model.train()
    epoch_loss = 0
    counter = 0
    for src, trg in iterator:
        counter += 1
        if counter % 500 == 0:
            print('[', counter, '/', len(iterator), ']')
        if torch.cuda.is_available():
          src, trg = src.cuda(), trg.cuda()

        optimizer.zero_grad()
        output = model(src, trg[:-1, :])

        loss = criterion(output.view(-1, output.shape[-1]), torch.reshape(trg[1:, :], (-1,)))
        loss.backward()
        optimizer.step()
        epoch_loss += loss.item()

    return epoch_loss / len(iterator)

# GENERAL FUNCTION FROM TRAINING AND VALIDATION
def train_all(model,optimizer,criterion,scheduler,epochs,best_eval_loss_cer, train_loader, val_loader,valid_loss_all,train_loss_all,eval_loss_cer_all,eval_accuracy_all,logging=True,epoch_limit=1000):
    train_loss = 0
    count_bad = 0
    confuse_dict = dict()
    for epoch in range(epochs, epoch_limit):
        print(f'Epoch: {epoch + 1:02}')
        start_time = time.time()
        print("-----------train------------")
        train_loss = train(model, optimizer, criterion, train_loader)
        print("\n-----------valid------------")
        valid_loss = evaluate(model, criterion, val_loader)
        print("-----------eval------------")
        eval_loss_cer, eval_accuracy, confuse_dict = validate(model, val_loader,confuse_dict=confuse_dict)
        scheduler.step(eval_loss_cer)
        valid_loss_all.append(valid_loss)
        train_loss_all.append(train_loss)
        eval_loss_cer_all.append(eval_loss_cer)
        eval_accuracy_all.append(eval_accuracy)
        pickle.dump(confuse_dict, open(path.log+'confuse_dict.pickle',mode='wb'))
        if eval_loss_cer < best_eval_loss_cer:
            count_bad = 0
            best_eval_loss_cer = eval_loss_cer
            torch.save({
                'model': model.state_dict(),
                'epoch': epoch,
                'best_eval_loss_cer': best_eval_loss_cer,
                'valid_loss_all': valid_loss_all,
                'train_loss_all': train_loss_all,
                'eval_loss_cer_all': eval_loss_cer_all,
                'eval_accuracy_all': eval_accuracy_all,
            }, path.log+'resnet50_trans_%.3f.pt' % (best_eval_loss_cer))
            print('Save best model')
        else:
            count_bad += 1
            torch.save({
                'model': model.state_dict(),
                'epoch': epoch,
                'best_eval_loss_cer': best_eval_loss_cer,
                'valid_loss_all': valid_loss_all,
                'train_loss_all': train_loss_all,
                'eval_loss_cer_all': eval_loss_cer_all,
                'eval_accuracy_all': eval_accuracy_all,
            }, path.log+'resnet50_trans_last.pt')
            print('Save model')

        if logging:
            wandb.log({'Train loss WER': train_loss, "Validation loss WER": valid_loss, 'Validation Word Accuracy': 100 - eval_accuracy,
                   'Validation loss CER': eval_loss_cer,'Learning Rate':scheduler._last_lr[0]})

        print(f'Time: {time.time() - start_time}s')
        print(f'Train Loss: {train_loss:.4f}')
        print(f'Val   Loss: {valid_loss:.4f}')
        print(f'Eval loss CER: {eval_loss_cer:.4f}')
        print(f'Eval accuracy: {100 - eval_accuracy:.4f}')
        if count_bad > 19:
            break



def validate(model, dataloader,confuse_dict):
    """
    params
    ---
    model : nn.Module
    dataloader :
    confuse_dict : dict
        to keep track of model's mistakes on symbols

    returns
    ---
    cer_overall / len(dataloader) * 100 : float
    wer_overall / len(dataloader) * 100 : float
    confuse_dict : dict
    """
    idx2char = dataloader.dataset.idx2char
    char2idx = dataloader.dataset.char2idx
    model.eval()
    show_count = 0
    wer_overall = 0
    cer_overall = 0
    with torch.no_grad():
        for (src, trg) in dataloader:
            img = np.moveaxis(src[0].numpy(), 0, 2)
            if torch.cuda.is_available():
              src = src.cuda()
            x = model.backbone.conv1(src)
            x = model.backbone.bn1(x)
            x = model.backbone.relu(x)
            x = model.backbone.maxpool(x)
            x = model.backbone.layer1(x)
            x = model.backbone.layer2(x)
            x = model.backbone.layer3(x)
            x = model.backbone.layer4(x)
            x = model.backbone.fc(x)

            x = x.permute(0, 3, 1, 2).flatten(2).permute(1, 0, 2)

            memory = model.transformer.encoder(model.pos_encoder(x))

            out_indexes = [char2idx['SOS'], ]

            for i in range(100):
                trg_tensor = torch.LongTensor(out_indexes).unsqueeze(1).to(device)

                output = model.fc_out(model.transformer.decoder(model.pos_decoder(model.decoder(trg_tensor)), memory))
                out_token = output.argmax(2)[-1].item()
                out_indexes.append(out_token)
                if out_token == char2idx['EOS']:
                    break

            out_char = labels_to_text(out_indexes[1:], idx2char)
            real_char = labels_to_text(trg[1:, 0].numpy(), idx2char)
            wer_overall += int(real_char != out_char)
            if out_char:
                cer = char_error_rate(real_char, out_char)
            else:
                cer = 1
            
            if len(out_char) == len(real_char):
              confuse_dict = confused_chars(real_char,out_char,confuse_dict)

            cer_overall += cer
            if show_count%2 == 0 and out_char != real_char:
                wandb.log({'Validation Character Accuracy': (1-cer)*100})
                wandb.log({"Validation Examples": wandb.Image(img, caption="Pred: {} Truth: {}".format(out_char, real_char))})
                show_count += 1
                print('Real:', real_char)
                print('Pred:', out_char)
                print(cer)
    
    return cer_overall / len(dataloader) * 100, wer_overall / len(dataloader) * 100, confuse_dict
