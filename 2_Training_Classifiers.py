import numpy as np
import pandas as pd
import seaborn as sns
import mne
import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import matplotlib.pyplot as plt
import pywt 
from PIL import Image
from utility import *
from model import *
from torchsummary import summary
import torch.optim.lr_scheduler as lr_scheduler


LOSS_POS_WEIGHT       = torch.tensor([1])

MODEL_FILE_DIRC_SateLight = MODEL_FILE_DIRC + "/SateLight"
MODEL_FILE_DIRC_CNN       = MODEL_FILE_DIRC + "/CNN"
os.makedirs(MODEL_FILE_DIRC_CNN, exist_ok=True)
os.makedirs(MODEL_FILE_DIRC_SateLight, exist_ok=True)

torch.manual_seed(3407)

eeg_num_list = list(range(1,98))

dataloaders, num_data = get_dataloader(eeg_num_list, shuffle=True, get_dataDWT=False)
    

num_train_data, num_valid_data, num_test_data = num_data
train_data, valid_data, test_data             = dataloaders

def start_classification_model_training(EPOCH_START, NUM_EPOCHS_CLASSIFIER, 
                                        model, MODEL_FILE_DIRC, model_name,  
                                        df, prev_best_valid_f1,prev_best_valid_loss,
                                        train_data, num_train_data, 
                                        valid_data, num_valid_data, 
                                        scheduler, optimizer, device, LOSS_POS_WEIGHT):
    count = 0
    for epoch in range(EPOCH_START, NUM_EPOCHS_CLASSIFIER):
            ## 1. Training
            model.train()
            train_loss, train_metric = train_classifier(model, train_data, device, num_train_data, optimizer, LOSS_POS_WEIGHT=LOSS_POS_WEIGHT) 
            
            ## 2. Evaluating
            model.eval()
            valid_loss, valid_metric = evaluate_classifier(model, valid_data, device, num_valid_data, LOSS_POS_WEIGHT=LOSS_POS_WEIGHT) 
            
            ## 3. Show the result
            list_data       = [train_loss, valid_loss]
            for key in ["precision", "accuracy", "f1_score", "recall"]:
                list_data.append(train_metric[key])
                list_data.append(valid_metric[key])
            df.loc[len(df)] = list_data
            
            print_log(f"> > > Epoch     : {epoch}", MODEL_FILE_DIRC)
            print_log(f"Train {'loss':<10}: {train_loss}", MODEL_FILE_DIRC)
            print_log(f"Valid {'loss':<10}: {valid_loss}", MODEL_FILE_DIRC)
            for key in ["precision", "accuracy", "f1_score", "recall"]:
                print_log(f"Train {key:<10}: {train_metric[key]}", MODEL_FILE_DIRC)
                print_log(f"Valid {key:<10}: {valid_metric[key]}", MODEL_FILE_DIRC)

            
            ## 3.1 Plot the loss function
            fig,ax = plt.subplots(3,2, figsize=(10,10))
            x_data = range(len(df["Train Loss"]))
            for i, key in enumerate(["Loss","precision", "accuracy", "f1_score", "recall"]):    
                ax[i%3][i//3].plot(x_data, df[f"Train {key}"], label=f"Train {key}")
                ax[i%3][i//3].plot(x_data, df[f"Valid {key}"], label=f"Valid {key}")
                ax[i%3][i//3].legend()
            plt.savefig(f'{MODEL_FILE_DIRC}/Loss.png', transparent=False, facecolor='white')
            plt.close('all')

            ## 3.3. Save model and Stoping criteria
            if prev_best_valid_f1 <= valid_metric["f1_score"]:  # If previous best validation f1-score <= current f1-score 
                state_dict = {
                    "model": model.state_dict(), 
                    "epoch":epoch,
                    "valid_f1_score": valid_metric["f1_score"],
                    "valid_loss": valid_loss
                }
                torch.save(state_dict, f"{MODEL_FILE_DIRC}/{model_name}_best.pt")
                prev_best_valid_f1 = valid_metric["f1_score"]  # Previous validation loss = Current validation loss
                count = 0
            else:
                count += 1
            
            if epoch % 5 == 0:
                state_dict = {
                    "model": model.state_dict(), 
                    "epoch":epoch,
                    "valid_f1_score": valid_metric["f1_score"],
                    "valid_loss": valid_loss
                }
                torch.save(state_dict, f"{MODEL_FILE_DIRC}/{model_name}_{epoch}.pt")
            
            df.to_csv(f"{MODEL_FILE_DIRC}/Loss.csv", index=False)
            
            if count == MAX_COUNT_F1_SCORE:
                print_log(f"The validation f1 score is not increasing for continuous {MAX_COUNT_F1_SCORE} time, so training stop", MODEL_FILE_DIRC)
                break
            
            scheduler.step()


def load_classification_model_dict(model, MODEL_FILE_DIRC, model_name):
    list_model = os.listdir(MODEL_FILE_DIRC) 
    if len(list_model) > 0:    # Load the latest trained model
        if os.path.exists(f"{MODEL_FILE_DIRC}/{model_name}_best.pt"):
            state_dict_loaded    = torch.load(f"{MODEL_FILE_DIRC}/{model_name}_best.pt")
            prev_best_valid_f1   = state_dict_loaded["valid_f1_score"]
            prev_best_valid_loss = state_dict_loaded["valid_loss"]
        list_model.remove(f"{model_name}_best.pt")
        num_list   = [int(model_dir[model_dir.rindex("_") +1: model_dir.rindex(".")]) for model_dir in list_model if model_dir.endswith(".pt")]
        num_max    = np.max(num_list)
        
        state_dict_loaded = torch.load(f"{MODEL_FILE_DIRC}/{model_name}_{num_max}.pt")
        model.load_state_dict(state_dict_loaded["model"])
        EPOCH_START = state_dict_loaded['epoch'] + 1
        
        print(f"The model has been loaded from the file '{model_name}_{num_max}.pt'")

        if os.path.exists(f"{MODEL_FILE_DIRC}/Loss.csv"):
            df = pd.read_csv(f"{MODEL_FILE_DIRC}/Loss.csv")
            df = df.iloc[:EPOCH_START-1, :]
            print(f"The dataframe that record the loss have been loaded from {MODEL_FILE_DIRC}/Loss.csv")

    else:
        EPOCH_START            = 1
        prev_best_valid_f1     = -1
        prev_best_valid_loss   = 10000
        df                     = pd.DataFrame(columns = ["Train Loss", "Valid Loss"] + \
                                                         flatten_concatenation([[f"Train {metric}", f"Valid {metric}"] for metric in ["precision", "accuracy", "f1_score", "recall"]]) )
    return model, df, EPOCH_START, prev_best_valid_f1, prev_best_valid_loss


model      = SateLight().to(device)
optimizer  = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)  
scheduler = lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)

# Get the following information:
# 1. Previous Trained model (if exist)
# 2. df that store the training/validation loss & metrics
# 3. epoch where the training start
# 4. Previous Highest Validation F1-score
# 5. Previous Lowest  Validation Loss
model, df, EPOCH_START, prev_best_valid_f1, prev_best_valid_loss = load_classification_model_dict(model, MODEL_FILE_DIRC_SateLight, "SateLight")

# Get the summary of the model
# print(summary(model, (19,1280)))

seperate = "\n" + "-" * 100 + "\n"
print(seperate + "Model infomation" + seperate)
print(f"Device used        :", device)
print(f"BATCH SIZE         :", BATCH_SIZE)
print(f"MAX_COUNT_F1_SCORE :", MAX_COUNT_F1_SCORE)
print(f"LEARNING RATE      :", LEARNING_RATE)
print(f"Prev Best f1-score in validation dataset:", prev_best_valid_f1)
print(f"Prev Best validation loss               :", prev_best_valid_loss)
print(f"Number of EPOCH for training   :",NUM_EPOCHS_CLASSIFIER, f"(EPOCH start from {EPOCH_START})")
print(f"Num of epochs of data for train:", num_train_data)
print(f"Num of epochs of data for valid:", num_valid_data)
print(f'Model parameters               : {sum(p.numel() for p in model.parameters()):,}' )


start_classification_model_training(EPOCH_START, NUM_EPOCHS_CLASSIFIER, 
                                    model, MODEL_FILE_DIRC_SateLight, "SateLight",
                                    df, prev_best_valid_f1, prev_best_valid_loss, 
                                    train_data, num_train_data, 
                                    valid_data, num_valid_data, 
                                    scheduler, optimizer, device, LOSS_POS_WEIGHT)

# Load the best model and turn to evaluation mode
model.eval()
state_dict_loaded = torch.load(f"{MODEL_FILE_DIRC_SateLight}/SateLight_best.pt")
model.load_state_dict(state_dict_loaded["model"])

test_loss, test_metric = evaluate_classifier(model, test_data, device, num_test_data) 

print_log(f"Best model is at epoch: {state_dict_loaded['epoch']}", MODEL_FILE_DIRC_SateLight)
print_log("Metric on testing dataset:", MODEL_FILE_DIRC_SateLight)
for key, value in test_metric.items():
    print_log(f"{key:<10}: {value:.4f}", MODEL_FILE_DIRC_SateLight)


### TRAINING CNN MODEL ###
model      = CNN().to(device)
optimizer  = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)  
scheduler  = lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)

# Get the following information:
# 1. Previous Trained model (if exist)
# 2. df that store the training/validation loss & metrics
# 3. epoch where the training start
# 4. Previous Highest Validation F1-score
# 5. Previous Lowest  Validation Loss
model, df, EPOCH_START, prev_best_valid_f1, prev_best_valid_loss = load_classification_model_dict(model, MODEL_FILE_DIRC_CNN, "CNN")

# Get the summary of the model
# print(summary(model, (19,1280)))

seperate = "\n" + "-" * 100 + "\n"
print(seperate + "Model infomation" + seperate)
print(f"Device used        :", device)
print(f"BATCH SIZE         :", BATCH_SIZE)
print(f"MAX_COUNT_F1_SCORE :", MAX_COUNT_F1_SCORE)
print(f"LEARNING RATE      :", LEARNING_RATE)
print(f"Prev Best recall in validation dataset:", prev_best_valid_f1)
print(f"Prev Best validation loss             :", prev_best_valid_loss)
print(f"Number of EPOCH for training   :",NUM_EPOCHS_CLASSIFIER, f"(EPOCH start from {EPOCH_START})")
print(f"Num of epochs of data for train:", num_train_data)
print(f"Num of epochs of data for valid:", num_valid_data)
print(f'Model parameters               : {sum(p.numel() for p in model.parameters()):,}' )

start_classification_model_training(EPOCH_START, NUM_EPOCHS_CLASSIFIER, 
                                    model, MODEL_FILE_DIRC_CNN, "CNN",
                                    df, prev_best_valid_f1, prev_best_valid_loss, 
                                    train_data, num_train_data, 
                                    valid_data, num_valid_data, 
                                    scheduler, optimizer, device, LOSS_POS_WEIGHT)

# Load the best model and turn to evaluation mode
model.eval()
state_dict_loaded = torch.load(f"{MODEL_FILE_DIRC_CNN}/CNN_best.pt")
model.load_state_dict(state_dict_loaded["model"])

test_loss, test_metric = evaluate_classifier(model, test_data, device, num_test_data) 

print_log(f"Best model is at epoch: {state_dict_loaded['epoch']}", MODEL_FILE_DIRC_CNN)
print_log("Metric on testing dataset:", MODEL_FILE_DIRC_CNN)
for key, value in test_metric.items():
    print_log(f"{key:<10}: {value:.4f}", MODEL_FILE_DIRC_CNN)